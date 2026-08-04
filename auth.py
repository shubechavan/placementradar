"""
auth.py — PlacementRadar CRM authentication (Phase 1)

Team accounts with per-user login. Security posture:
  - Passwords hashed with werkzeug PBKDF2 (never stored or logged in plaintext).
  - Login state lives in Flask's signed, HttpOnly session cookie (server secret).
  - Constant-time hash comparison via check_password_hash.
  - Inactive/deleted users are rejected even with a valid session.
  - Role-based access: @admin_required gates user-management endpoints.

The SECRET_KEY that signs sessions MUST come from the environment in production.
A random per-process key is used as a dev fallback (logs a warning) — that only
means sessions reset on restart locally, never a hardcoded secret in the repo.
"""
import os
import secrets
from functools import wraps
from flask import session, jsonify, request
from werkzeug.security import generate_password_hash, check_password_hash

from database import (get_user_by_email, get_user_by_id, create_user,
                      count_users)

# Minimum password length — cheap defense against trivial credentials.
MIN_PASSWORD_LEN = 8

# ── Production vs dev ──────────────────────────────────────────────────────────
# Production is defined by SECRET_KEY being set (Render sets it; local dev usually
# doesn't). This is the ONE signal that decides whether the app may ever fall open.
IS_PRODUCTION = bool(os.environ.get('SECRET_KEY'))

# Cache for "does any user account exist?" so the hot request path does not hit
# MongoDB on every call. Flips to True the moment the first user is created and
# never needs to flip back (deleting all users to re-open the app is not a flow
# we support in production). Call note_user_created() after creating a user.
_users_exist_cache = None

def any_users_exist():
    global _users_exist_cache
    if _users_exist_cache:
        return True
    # Only touch the DB while we still believe there are none.
    _users_exist_cache = count_users() > 0
    return _users_exist_cache

def note_user_created():
    """Call after a user is created so the cache reflects it without a DB hit."""
    global _users_exist_cache
    _users_exist_cache = True

def auth_disabled():
    """True only in local dev with no accounts and no API key — the ONLY case
    where the app is allowed to run without authentication. In production
    (SECRET_KEY set) this is ALWAYS False, so a missing admin seed can never
    leave the app open."""
    if IS_PRODUCTION:
        return False
    if os.environ.get('APP_API_KEY'):
        return False
    return not any_users_exist()


def init_app_secret(app):
    """Configure the signing secret and hardened session-cookie flags."""
    secret = os.environ.get('SECRET_KEY', '')
    if not secret:
        # Dev fallback: ephemeral random secret. NOT hardcoded, NOT persisted.
        # In production SECRET_KEY must be set or sessions won't survive restarts.
        secret = secrets.token_hex(32)
        print('[WARN] SECRET_KEY not set — using an ephemeral dev secret; '
              'sessions reset on restart. Set SECRET_KEY in production.')
    app.secret_key = secret
    # Secure cookies require HTTPS. Enable them only when explicitly in
    # production (SESSION_COOKIE_SECURE=1) — NOT merely because a SECRET_KEY is
    # set, otherwise a local HTTPS-less run silently drops the cookie and login
    # appears to fail. Render terminates TLS, so set this env var there.
    secure_cookies = os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes')
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,   # JS can't read the cookie (XSS mitigation)
        SESSION_COOKIE_SAMESITE='Lax',  # CSRF mitigation for the session cookie
        SESSION_COOKIE_SECURE=secure_cookies,
    )


def hash_password(plain):
    return generate_password_hash(plain)


def verify_password(stored_hash, plain):
    try:
        return check_password_hash(stored_hash, plain)
    except Exception:
        return False


def validate_password(plain):
    """Return an error string if the password is unacceptable, else None."""
    if not plain or len(plain) < MIN_PASSWORD_LEN:
        return f'Password must be at least {MIN_PASSWORD_LEN} characters.'
    return None


def login_user(user):
    """Record the authenticated user in the signed session."""
    session.clear()
    session['uid'] = user['_id']
    session['role'] = user.get('role', 'member')
    session.permanent = True


def logout_user():
    session.clear()


def current_user():
    """Resolve the logged-in user from the session, or None.

    Re-checks the database every request so a deactivated or deleted account
    loses access immediately, even if the cookie is still valid.
    """
    uid = session.get('uid')
    if not uid:
        return None
    user = get_user_by_id(uid)
    if not user or not user.get('active', True):
        return None
    return user


def bootstrap_first_admin():
    """Seed the first admin from env vars if no users exist yet.

    Lets a fresh deployment create the sir's account without a chicken-and-egg
    problem. Credentials come from ADMIN_EMAIL / ADMIN_PASSWORD (env only) and
    are used exactly once — when the users collection is empty.
    """
    if count_users() > 0:
        note_user_created()
        return

    email = os.environ.get('ADMIN_EMAIL', '').strip().lower()
    password = os.environ.get('ADMIN_PASSWORD', '')

    def _fatal_or_warn(msg):
        # In production a missing/invalid admin means the app would have NO users.
        # Combined with the auth guard that now never falls open in prod, that is
        # a locked-out-but-safe state — make it impossible to miss in the logs.
        banner = '=' * 64
        if IS_PRODUCTION:
            print(f'\n{banner}\n[AUTH][FATAL] {msg}\n'
                  '  The app is running in PRODUCTION (SECRET_KEY set) with NO admin\n'
                  '  account. Nobody can log in. Set ADMIN_EMAIL and a valid\n'
                  f'  ADMIN_PASSWORD (>= {MIN_PASSWORD_LEN} chars) and restart.\n{banner}\n')
        else:
            print(f'[AUTH] {msg} (dev mode — app stays open until you add a user)')

    if not email or not password:
        _fatal_or_warn('ADMIN_EMAIL / ADMIN_PASSWORD not set — no admin seeded.')
        return
    if validate_password(password):
        _fatal_or_warn('ADMIN_PASSWORD is too short — admin NOT created.')
        return
    uid = create_user(os.environ.get('ADMIN_NAME', 'Admin'), email,
                      hash_password(password), role='admin')
    if uid:
        note_user_created()
        print(f'[AUTH] Seeded first admin: {email}')
    else:
        _fatal_or_warn(f'Could not create admin {email} (duplicate?).')


# ── Route guards ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if auth_disabled():                 # local dev, no accounts → allow
            return f(*args, **kwargs)
        if current_user() is None:
            return jsonify({'error': 'authentication required'}), 401
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if auth_disabled():                 # local dev, no accounts → allow
            return f(*args, **kwargs)
        user = current_user()
        if user is None:
            return jsonify({'error': 'authentication required'}), 401
        if user.get('role') != 'admin':
            return jsonify({'error': 'admin access required'}), 403
        return f(*args, **kwargs)
    return wrapper
