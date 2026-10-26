// App shell: sign-in gate, rail / drawer navigation, hash router.
import { html, useState, useEffect, api, authEvents, FEATURES } from './lib.js';
import { Icon, Toasts } from './ui.js';
import { Today } from './views/today.js';
import { Board } from './views/board.js';
import { Pipeline } from './views/pipeline.js';
import { HrDatabase } from './views/hr.js';
import { JobSearch } from './views/search.js';
import { Outreach } from './views/outreach.js';
import { CustomSites } from './views/sites.js';
import { Settings } from './views/settings.js';
import { Leaderboard, Members } from './views/admin.js';

const NAV = [
  { group: null, items: [
    { id: 'today', label: 'Today', icon: 'today' },
    { id: 'search', label: 'Job search', icon: 'search' },
    { id: 'board', label: 'Job board', icon: 'board' },
    { id: 'pipeline', label: 'Pipeline', icon: 'pipeline' },
  ] },
  { group: 'Outreach', items: [
    { id: 'outreach', label: 'Email outreach', icon: 'mail', feature: 'emailOutreach' },
    { id: 'hr', label: 'HR database', icon: 'book' },
  ] },
  { group: 'Setup', items: [
    { id: 'sites', label: 'Custom sites', icon: 'globe', feature: 'customSites' },
    { id: 'settings', label: 'Settings', icon: 'settings' },
  ] },
  { group: 'Admin', admin: true, items: [
    { id: 'leaderboard', label: 'Leaderboard', icon: 'chart' },
    { id: 'members', label: 'Members', icon: 'users' },
  ] },
// A hidden feature leaves the menu and its links: an unknown route falls back to Today.
].map((g) => ({ ...g, items: g.items.filter((i) => !i.feature || FEATURES[i.feature]) }));
const LABELS = Object.fromEntries(NAV.flatMap((g) => g.items.map((i) => [i.id, i.label])));

// ── Router: #/view?key=value. Links push history; filters replace it. ──
function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [path, query] = h.split('?');
  const view = LABELS[path] ? path : 'today';
  return { view, params: Object.fromEntries(new URLSearchParams(query || '')) };
}
function useRoute() {
  const [route, setRoute] = useState(parseHash());
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  const setParams = (p) => {
    const clean = Object.fromEntries(Object.entries(p).filter(([, v]) => v !== '' && v != null));
    const qs = new URLSearchParams(clean).toString();
    history.replaceState(null, '', `#/${route.view}${qs ? '?' + qs : ''}`);
    setRoute({ view: route.view, params: clean });
  };
  return [route, setParams];
}

function Brand() {
  return html`<a className="brand" href="#/today" aria-label="PlacementRadar, KJSIM. Go to Today">
    <span className="brand-badge">KJSIM</span><span className="brand-name">PlacementRadar</span></a>`;
}

function Rail({ user, view, open, onNavigate, onSignOut }) {
  const isAdmin = user && user.role === 'admin';
  return html`<nav className=${'rail' + (open ? ' is-open' : '')} aria-label="Main" id="rail">
    <${Brand} />
    <div className="nav">
      ${NAV.filter((g) => !g.admin || isAdmin).map((g) => html`<div key=${g.group || 'main'} role="group" aria-label=${g.group || 'Work'}>
        ${g.group && html`<div className="nav-group" aria-hidden="true">${g.group}</div>`}
        ${g.items.map((i) => html`<a key=${i.id} href=${'#/' + i.id} aria-current=${view === i.id ? 'page' : null} onClick=${onNavigate}>
          <${Icon} name=${i.icon} />${i.label}</a>`)}
      </div>`)}
    </div>
    ${user && html`<div className="rail-foot">
      <div className="who"><b>${user.name || user.email}</b><span className="muted small">${user.role === 'admin' ? 'Admin' : 'Committee member'}</span></div>
      <button type="button" className="btn btn-quiet btn-icon" onClick=${onSignOut} aria-label="Sign out" title="Sign out"><${Icon} name="signout" /></button>
    </div>`}
  </nav>`;
}

function AdminOnly({ view }) {
  return html`<div className="page"><header className="page-head"><h1>${LABELS[view]}</h1></header>
    <p className="lede">Only admins can open ${LABELS[view]}. Ask the placement head if you need access.</p></div>`;
}

function Login({ onDone }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault(); setBusy(true); setErr('');
    try { const d = await api.post('/api/auth/login', { email, password }); onDone(d.user); }
    catch (x) { setErr(x.message === 'Invalid email or password' ? 'That email and password don’t match. Check both and try again.' : x.message); setBusy(false); }
  }
  return html`<main className="login">
    <form className="login-box" onSubmit=${submit} noValidate>
      <${Brand} />
      <h1>Sign in to the placement committee workspace</h1>
      <div className="field"><label for="li-email">Email</label>
        <input id="li-email" className="input" type="email" autocomplete="username" required value=${email} onInput=${(e) => setEmail(e.target.value)} /></div>
      <div className="field"><label for="li-pass">Password</label>
        <input id="li-pass" className="input" type="password" autocomplete="current-password" required value=${password} onInput=${(e) => setPassword(e.target.value)} /></div>
      ${err && html`<p className="formerr" role="alert">${err}</p>`}
      <button type="submit" className="btn btn-primary" disabled=${busy}>${busy ? 'Signing in…' : 'Sign in'}</button>
      <p className="muted small">No account? Ask the placement head to add you under Members.</p>
    </form></main>`;
}

function App() {
  const [auth, setAuth] = useState({ checked: false, user: null, needsLogin: false });
  const [route, setParams] = useRoute();
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    api.get('/api/auth/me')
      .then((d) => setAuth({ checked: true, user: d.user, needsLogin: !d.user && d.needs_login }))
      .catch(() => setAuth({ checked: true, user: null, needsLogin: true }));
    const expired = () => setAuth((a) => ({ ...a, user: null, needsLogin: true }));
    authEvents.addEventListener('expired', expired);
    return () => authEvents.removeEventListener('expired', expired);
  }, []);
  useEffect(() => { document.title = `${LABELS[route.view]}, PlacementRadar`; setDrawer(false); }, [route.view]);
  useEffect(() => {
    if (!drawer) return;
    const esc = (e) => { if (e.key === 'Escape') setDrawer(false); };
    document.addEventListener('keydown', esc);
    const first = document.querySelector('#rail a');
    first && first.focus();
    return () => document.removeEventListener('keydown', esc);
  }, [drawer]);

  if (!auth.checked) return null;
  if (auth.needsLogin) return html`<${Login} onDone=${(u) => setAuth({ checked: true, user: u, needsLogin: false })} /><${Toasts} />`;

  async function signOut() {
    try { await api.post('/api/auth/logout'); } catch (e) { /* signing out locally anyway */ }
    setAuth({ checked: true, user: null, needsLogin: true });
  }
  const { view, params } = route;
  const user = auth.user || { name: 'Local dev', email: '', role: 'admin' };   // auth disabled in local dev
  const isAdmin = user.role === 'admin';
  const p = { user, params, setParams };
  const VIEWS = {
    today: Today, search: JobSearch, board: Board, pipeline: Pipeline, outreach: Outreach, hr: HrDatabase,
    sites: CustomSites, settings: Settings, leaderboard: isAdmin ? Leaderboard : null, members: isAdmin ? Members : null,
  };
  const View = VIEWS[view];
  const body = View ? html`<${View} key=${view} ...${p} />` : html`<${AdminOnly} view=${view} />`;

  return html`<div className="shell">
    <button type="button" className="vh btn" style=${{ position: 'fixed', top: 8, left: 8, zIndex: 80 }}
      onFocus=${(e) => e.target.classList.remove('vh')} onBlur=${(e) => e.target.classList.add('vh')}
      onClick=${() => document.getElementById('main').focus()}>Skip to content</button>
    <${Rail} user=${user} view=${view} open=${drawer} onNavigate=${() => setDrawer(false)} onSignOut=${signOut} />
    ${drawer && html`<div className="scrim" style=${{ zIndex: 54 }} onClick=${() => setDrawer(false)}></div>`}
    <div className="main">
      <header className="topbar">
        <button type="button" className="btn btn-quiet btn-icon" onClick=${() => setDrawer(true)} aria-label="Open menu" aria-expanded=${drawer} aria-controls="rail"><${Icon} name="menu" size=${22} /></button>
        <span className="t-title">${LABELS[view]}</span>
        <span className="brand-badge" aria-hidden="true">KJSIM</span>
      </header>
      <main id="main" tabIndex="-1">${body}</main>
    </div>
    <${Toasts} />
  </div>`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
