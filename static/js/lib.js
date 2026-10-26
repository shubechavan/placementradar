// Shared helpers: React + htm binding, API calls, formatting, labels.
// React, ReactDOM and htm are vendored UMD globals loaded (deferred) before this module.
const { useState, useEffect, useMemo, useRef, useCallback, Fragment } = React;
export const html = htm.bind(React.createElement);
export { useState, useEffect, useMemo, useRef, useCallback, Fragment };

// ── API ──────────────────────────────────────────────────────────────────
// Every call goes through here so a 401 (session expired) sends the user back
// to the sign-in screen instead of failing silently.
export const authEvents = new EventTarget();

async function request(method, url, body) {
  let res;
  try {
    res = await fetch(url, {
      method, credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new Error('Could not reach the server. Check your connection and try again.');
  }
  if (res.status === 401 && !url.startsWith('/api/auth/')) {
    authEvents.dispatchEvent(new Event('expired'));
    throw new Error('Your session ended. Sign in again to continue.');
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON body */ }
  if (!res.ok) throw new Error(data.error || `The server returned an error (${res.status}).`);
  return data;
}
export const api = {
  get: (u) => request('GET', u),
  post: (u, b = {}) => request('POST', u, b),
  patch: (u, b = {}) => request('PATCH', u, b),
  del: (u) => request('DELETE', u),
};

// ── Toasts ───────────────────────────────────────────────────────────────
export const toastEvents = new EventTarget();
export function toast(text, tone = 'info') {
  toastEvents.dispatchEvent(new CustomEvent('toast', { detail: { text, tone, id: Math.random() } }));
}

// ── Labels (display only; stored values never change) ────────────────────
// Status values are what the API stores. Pipeline stage "interested" stores
// status "replied", so both screens show the status as "Replied".
export const STATUSES = [
  ['new', 'To contact'],
  ['contacted', 'Contacted'],
  ['replied', 'Replied'],
  ['drive_scheduled', 'Drive scheduled'],
  ['placed', 'Placed'],
  ['rejected', 'Rejected'],
  ['not_interested', 'Not interested'],
];
const STATUS_LABEL = Object.fromEntries(STATUSES);
export const statusLabel = (s) => STATUS_LABEL[s || 'new'] || s;

// Activity outcomes accepted by POST /api/activities (database.VALID_OUTCOMES).
// "Interested" (a call outcome) and "Reply received" (an email reply) are
// different values in the data, so both keep their own label.
export const OUTCOME_LABEL = {
  interested: 'Interested', not_interested: 'Not interested', callback: 'Call back later',
  no_answer: 'No answer', reply_received: 'Reply received', drive_scheduled: 'Drive scheduled',
  contacted: 'Contacted', '': 'No outcome',
};
export const CHANNEL_LABEL = { call: 'Call', email: 'Email', whatsapp: 'WhatsApp', note: 'Note' };

// ── Contact helpers ──────────────────────────────────────────────────────
const blank = (v) => !v || !String(v).trim() || v === 'nan' || v === '—';
export const list = (v) => blank(v) ? [] : String(v).split(',').map((s) => s.trim()).filter((s) => s && s !== 'nan');
export const phones = (r) => list(r.phone);
export const emails = (r) => list(r.emails);
export const primaryEmail = (r) => (!blank(r.hr_contact) ? r.hr_contact.trim() : emails(r)[0] || '');
export const hasContact = (r) => phones(r).length > 0 || emails(r).length > 0;

// tel: targets are rebuilt from digits and a leading + only: scraped text never reaches an href.
export function telHref(s) {
  const t = String(s || '').replace(/[^0-9+]/g, '');
  return /^\+?\d{6,15}$/.test(t) ? 'tel:' + t : '';
}
export function mailHref(s) {
  const t = String(s || '').trim();
  return /^[^\s@<>"']+@[^\s@<>"']+\.[^\s@<>"']+$/.test(t) ? 'mailto:' + t : '';
}
export function safeUrl(u) {
  u = String(u || '').trim();
  return /^https?:\/\//i.test(u) ? u : '';
}

// Contact grade, same scoring the old board used.
export function grade(r) {
  let s = 0;
  if (phones(r).length) s += 35;
  if (emails(r).length) s += 35;
  if (!blank(r.company_url)) s += 15;
  if (/hr|recruit|talent|hiring|placement/i.test(r.emails || '')) s += 15;
  return s >= 80 ? 'A' : s >= 55 ? 'B' : s >= 35 ? 'C' : s >= 15 ? 'D' : 'F';
}
export const GRADE_ORDER = { A: 0, B: 1, C: 2, D: 3, F: 4 };
export const GRADE_HELP = {
  A: 'Phone and email found, with an HR-type address',
  B: 'Phone and email found',
  C: 'Only a phone or only an email',
  D: 'Company website only',
  F: 'No contact details yet',
};

export function initials(name) {
  const words = String(name || '').replace(/[^A-Za-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  return (words[0][0] + (words[1] ? words[1][0] : '')).toUpperCase();
}

// ── Dates ────────────────────────────────────────────────────────────────
export function daysSince(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d)) return null;
  return Math.floor((Date.now() - d.getTime()) / 864e5);
}
export function ago(iso) {
  const n = daysSince(iso);
  if (n === null) return '';
  if (n <= 0) return 'today';
  if (n === 1) return 'yesterday';
  return `${n} days ago`;
}
export function shortDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}
export function dateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return d.toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}
export const plural = (n, one, many) => `${n.toLocaleString('en-IN')} ${n === 1 ? one : many || one + 's'}`;

// ── Per-device counter for "logged today" (there is no per-user endpoint) ──
export function loggedToday(email) {
  try {
    const raw = JSON.parse(localStorage.getItem('pr.logged') || '{}');
    const key = new Date().toDateString() + '|' + (email || '');
    return raw[key] || 0;
  } catch (e) { return 0; }
}
export function bumpLoggedToday(email) {
  try {
    const key = new Date().toDateString() + '|' + (email || '');
    const raw = JSON.parse(localStorage.getItem('pr.logged') || '{}');
    const next = { [key]: (raw[key] || 0) + 1 };
    localStorage.setItem('pr.logged', JSON.stringify(next));
  } catch (e) { /* storage blocked: the count just won't show */ }
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand('copy'); } catch (_) {}
    ta.remove(); return ok;
  }
}

// True below the 900px breakpoint used in app.css.
export function useIsPhone() {
  const mq = window.matchMedia('(max-width: 900px)');
  const [on, setOn] = useState(mq.matches);
  useEffect(() => { const f = () => setOn(mq.matches); mq.addEventListener('change', f); return () => mq.removeEventListener('change', f); }, []);
  return on;
}
