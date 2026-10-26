// Settings: integrations first, then college details. Templates live on Email outreach.
import { html, useState, useEffect, api, toast, FEATURES } from '../lib.js';
import { ErrorLine } from '../ui.js';

const SOURCES = [
  ['Company websites', 'Reads /careers, /jobs and /contact pages and keeps emails that match the company’s own domain.'],
  ['Deep site crawl', 'Follows up to 12 internal links, contact and team pages first, to find phone numbers.'],
  ['Zauba Corp registry', 'Government company register: the registered email and phone of Indian private and public limited companies.'],
  ['Sulekha directory', 'Business directory with phone numbers for small and large companies in every city.'],
  ['TradeIndia', 'B2B directory. Opens company pages for a second try when the search page has no phone.'],
  ['Job description scan', 'Looks for phone, WhatsApp numbers and email links inside the job description.'],
  ['HR database', 'The committee’s own HR contacts. Grows with every search.'],
  ['Job page fetch', 'When a description is short, opens the full job page on LinkedIn, Naukri or Internshala.'],
  ['Email check', 'Guesses common HR address patterns and checks each with the mail server. No email is sent.'],
];
const KEYS = ['gmail_email', 'gmail_app_password', 'serper_api_key', 'college_name', 'committee_name', 'committee_email'];

function SourceHealth() {
  const [rows, setRows] = useState(null);
  useEffect(() => { api.get('/api/source-health').then((d) => setRows(d.sources || [])).catch(() => setRows([])); }, []);
  if (rows === null) return html`<p className="muted">Loading source health…</p>`;
  if (!rows.length) return html`<p className="muted">No source data recorded yet.</p>`;
  const pct = (v) => (v == null ? 'No tries yet' : `${Math.round(v * 100)}%`);
  return html`<div className="table-wrap"><table className="ledger-table is-static"><thead><tr>
      <th className="c-co">Source</th><th>Tries</th><th>Worked</th><th>Found</th><th>Blocked or failed</th></tr></thead>
    <tbody>${rows.map((r) => html`<tr key=${r.source_id}>
      <td className="c-co"><span className="co-name">${r.source_id.replace(/^board:/, '').replace(/_/g, ' ')}</span>
        ${r.source_id.startsWith('board:') && html` <span className="muted">job board, counts jobs</span>`}</td>
      <td className="num">${r.attempts.toLocaleString('en-IN')}</td><td className="num">${pct(r.success_rate)}</td>
      <td className="num">${r.contacts_found.toLocaleString('en-IN')}</td>
      <td>${r.blocked ? html`<span className="flag">Mostly blocked (${pct(r.non_200_rate)})</span>` : html`<span className="num">${pct(r.non_200_rate)}</span>`}</td></tr>`)}</tbody>
  </table></div>`;
}

export function Settings({ user }) {
  const isAdmin = user && user.role === 'admin';
  const [s, setS] = useState(null);
  const [orig, setOrig] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [testing, setTesting] = useState(false);

  async function load() {
    try { const d = await api.get('/api/settings'); const v = Object.fromEntries(KEYS.map((k) => [k, d[k] || ''])); setS(v); setOrig(v); setError(''); }
    catch (e) { setError(e.message); }
  }
  useEffect(() => { load(); }, []);
  const dirty = s && orig && KEYS.some((k) => s[k] !== orig[k]);
  const field = (k, label, opts = {}) => html`<div className="field">
    <label for=${'st-' + k}>${label}</label>
    <input id=${'st-' + k} className="input" type=${opts.type || 'text'} value=${s[k]} disabled=${!isAdmin}
      autocomplete=${opts.type === 'password' ? 'new-password' : 'off'} placeholder=${opts.ph || ''} onInput=${(e) => setS({ ...s, [k]: e.target.value })} />
    ${opts.hint && html`<span className="hint">${opts.hint}</span>`}</div>`;

  async function save(e) {
    e.preventDefault(); setBusy(true); setNote('');
    try {
      const body = Object.fromEntries(KEYS.filter((k) => s[k] !== orig[k]).map((k) => [k, s[k]]));
      const d = await api.post('/api/settings', body);
      toast('Settings saved.'); if (d.message) setNote(d.message);
      await load();
    } catch (x) { toast(`Settings not saved. ${x.message}`, 'error'); }
    setBusy(false);
  }
  async function testGmail() {
    setTesting(true);
    try {
      const d = await api.post('/api/settings/test-smtp', { gmail_email: s.gmail_email, gmail_app_password: s.gmail_app_password });
      d.success ? toast(d.message || 'Gmail connected.') : toast(d.error || 'Gmail didn’t accept the address and app password.', 'error');
    } catch (x) { toast(x.message, 'error'); }
    setTesting(false);
  }

  return html`<div className="page">
    <header className="page-head"><h1>Settings</h1></header>
    ${!isAdmin && html`<p className="lede">Only admins can change settings. Ask the placement head if something here is wrong.</p>`}
    ${error ? html`<${ErrorLine} message=${'Settings didn’t load. ' + error} onRetry=${load} />` : !s ? html`<div className="skeleton-row"></div>` : html`
    <form onSubmit=${save} noValidate>
      ${FEATURES.emailOutreach && html`<section className="section settings-sec">
        <div className="section-head"><h2>Gmail for outreach</h2><span className="why">sends cold emails and follow-ups</span></div>
        <div className="settings-grid">
          ${field('gmail_email', 'Gmail address', { type: 'email', ph: 'placement@kjsim.edu.in' })}
          ${field('gmail_app_password', 'App password', { type: 'password', ph: 'xxxx xxxx xxxx xxxx', hint: html`Not your normal Gmail password. <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">Create an app password</a>. Some servers only accept it as an environment variable; you’ll be told if so.` })}
        </div>
        ${isAdmin && html`<button type="button" className="btn btn-sm" onClick=${testGmail} disabled=${testing || !s.gmail_email}>${testing ? 'Testing…' : 'Test Gmail'}</button>`}
      </section>`}
      <section className="section settings-sec">
        <div className="section-head"><h2>Google Jobs search</h2><span className="why">2,500 free searches a month</span></div>
        <div className="settings-grid">
          ${field('serper_api_key', 'Serper.dev API key', { type: 'password', hint: html`Get a free key at <a href="https://serper.dev" target="_blank" rel="noopener noreferrer">serper.dev</a>. Google Jobs is paused on the server for now: it returned job-board landing pages, not jobs.` })}
        </div>
      </section>
      <section className="section settings-sec">
        <div className="section-head"><h2>College details</h2><span className="why">used in email templates</span></div>
        <div className="settings-grid">
          ${field('college_name', 'College name', { ph: 'K J Somaiya Institute of Management' })}
          ${field('committee_name', 'Committee name', { ph: 'Training and Placement Cell' })}
          ${field('committee_email', 'Committee email', { type: 'email', ph: 'placement@kjsim.edu.in' })}
        </div>
        ${FEATURES.emailOutreach && html`<p className="muted">Email templates are edited on <a href="#/outreach">Email outreach</a>.</p>`}
      </section>
      ${isAdmin && html`<div className="save-bar">
        <button type="submit" className="btn btn-primary" disabled=${busy || !dirty}>${busy ? 'Saving…' : 'Save settings'}</button>
        <span className="muted">${dirty ? 'You have unsaved changes.' : 'All changes saved.'}</span></div>`}
      ${note && html`<p className="formerr" role="alert">${note}</p>`}
    </form>`}
    <section className="section">
      <div className="section-head"><h2>How contacts are found</h2><span className="why">Find contacts runs these together</span></div>
      <dl className="facts sources-list">${SOURCES.map(([n, d]) => html`<dt key=${n + 't'}>${n}</dt><dd key=${n + 'd'}>${d}</dd>`)}</dl>
      ${isAdmin && html`<details className="advanced" style=${{ marginTop: 16 }}><summary>Source health <span className="hint">which scrapers are working</span></summary>
        <div style=${{ marginTop: 12 }}><${SourceHealth} /></div></details>`}
    </section>
  </div>`;
}
