// Email outreach: templates, cold emails, follow-ups and the sent log.
import { html, useState, useEffect, useMemo, api, toast, primaryEmail, statusLabel, plural, dateTime, ago } from '../lib.js';
import { Status, Confirm, Empty, ErrorLine } from '../ui.js';

const TEMPLATES = [['outreach_template', 'Cold outreach'], ['followup_template', 'Follow-up']];
const STEP = 40;

function Templates({ settings, isAdmin, onSaved }) {
  const [key, setKey] = useState('outreach_template');
  const [text, setText] = useState(settings[key] || '');
  const [busy, setBusy] = useState(false);
  useEffect(() => { setText(settings[key] || ''); }, [key, settings]);
  async function save() {
    setBusy(true);
    try { await api.post('/api/settings', { [key]: text }); toast('Template saved.'); onSaved({ [key]: text }); }
    catch (e) { toast(`Template not saved. ${e.message}`, 'error'); }
    setBusy(false);
  }
  return html`<section className="section">
    <div className="section-head"><h2>Templates</h2>
      <div className="section-actions"><div className="seg" role="group" aria-label="Template">
        ${TEMPLATES.map(([k, l]) => html`<button key=${k} type="button" aria-pressed=${key === k} onClick=${() => setKey(k)}>${l}</button>`)}
      </div></div></div>
    <div className="field" style=${{ marginTop: 14 }}>
      <label for="tpl-body">${TEMPLATES.find(([k]) => k === key)[1]} email</label>
      <textarea id="tpl-body" className="textarea tpl" value=${text} readOnly=${!isAdmin} onInput=${(e) => setText(e.target.value)}></textarea>
      <span className="hint">These words are replaced for each company: {{company}}, {{college_name}}, {{committee_name}}, {{committee_email}}. The subject line is set by the server.</span>
    </div>
    <div style=${{ marginTop: 10 }}>${isAdmin
      ? html`<button type="button" className="btn" onClick=${save} disabled=${busy || text === (settings[key] || '')}>${busy ? 'Saving…' : 'Save template'}</button>`
      : html`<p className="muted">Only admins can change templates.</p>`}</div>
  </section>`;
}

function Recipients({ jobs, template, onSent }) {
  const [only, setOnly] = useState('new');
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(new Set());
  const [shown, setShown] = useState(STEP);
  const [ask, setAsk] = useState(false), [busy, setBusy] = useState(false);
  const list = useMemo(() => jobs.filter((j) => primaryEmail(j) && (only === 'all' || (j.status || 'new') === 'new')
    && (!q || `${j.company} ${j.title}`.toLowerCase().includes(q.toLowerCase()))), [jobs, only, q]);
  useEffect(() => { setSel(new Set(list.map((j) => j.id))); setShown(STEP); }, [only, jobs]);
  const chosen = list.filter((j) => sel.has(j.id));
  async function send() {
    setBusy(true);
    try {
      const d = await api.post('/api/outreach/bulk', { template, recipients: chosen.map((j) => ({ email: primaryEmail(j), company: j.company, title: j.title, job_id: j.id })) });
      const ok = (d.results || []).filter((r) => r.success).length, bad = (d.results || []).length - ok;
      toast(bad ? `Sent ${ok}. ${bad} failed: check the Gmail settings.` : `Sent ${plural(ok, 'email')}.`, bad ? 'error' : 'info');
      setAsk(false); onSent();
    } catch (e) { toast(e.message, 'error'); }
    setBusy(false);
  }
  return html`<section className="section">
    <div className="section-head"><h2>Send cold outreach</h2><span className="count">${chosen.length}</span><span className="why">of ${plural(list.length, 'company', 'companies')} picked</span>
      <div className="section-actions"><button type="button" className="btn btn-primary btn-sm" disabled=${!chosen.length || !template.trim()} onClick=${() => setAsk(true)}>Send email to ${chosen.length}</button></div></div>
    <div className="toolbar" style=${{ marginTop: 12 }}>
      <div className="seg" role="group" aria-label="Which companies">
        <button type="button" aria-pressed=${only === 'new'} onClick=${() => setOnly('new')}>To contact only</button>
        <button type="button" aria-pressed=${only === 'all'} onClick=${() => setOnly('all')}>Every status</button></div>
      <label className="vh" for="rc-q">Search companies</label>
      <input id="rc-q" className="input" type="search" placeholder="Search company or role" value=${q} onInput=${(e) => setQ(e.target.value)} />
      <button type="button" className="btn btn-quiet btn-sm" onClick=${() => setSel(new Set(list.map((j) => j.id)))}>Pick all</button>
      <button type="button" className="btn btn-quiet btn-sm" onClick=${() => setSel(new Set())}>Pick none</button>
    </div>
    ${!template.trim() && html`<p className="formerr">The Cold outreach template is empty. Write it above before sending.</p>`}
    ${!list.length ? html`<${Empty} title="No companies with an email here.">${only === 'new' ? 'Switch to Every status, or use Find contacts on the Job board to find more emails.' : 'Use Find contacts on the Job board to find emails.'}</${Empty}>`
    : html`<ul className="ledger pick-list">${list.slice(0, shown).map((j) => html`<li key=${j.id}><label className="pick">
        <input type="checkbox" className="check" checked=${sel.has(j.id)} onChange=${() => setSel((s) => { const n = new Set(s); n.has(j.id) ? n.delete(j.id) : n.add(j.id); return n; })} />
        <span className="pick-co"><b>${j.company || 'Unnamed company'}</b><span className="muted">${j.title || ''}</span></span>
        <span className="pick-mail">${primaryEmail(j)}</span><${Status} value=${j.status} /></label></li>`)}</ul>
      ${list.length > shown && html`<div className="ledger-more"><button type="button" className="btn btn-sm" onClick=${() => setShown((n) => n + STEP)}>Show ${Math.min(STEP, list.length - shown)} more</button></div>`}`}
    ${ask && html`<${Confirm} title=${`Email ${plural(chosen.length, 'company', 'companies')}?`} busy=${busy}
      body=${`This sends the Cold outreach template above from the committee Gmail account. Each company moves to Contacted once its email is sent.`}
      confirmLabel=${`Send ${plural(chosen.length, 'email')}`} onConfirm=${send} onClose=${() => setAsk(false)} />`}
  </section>`;
}

function Followups() {
  const [days, setDays] = useState('3');
  const [rows, setRows] = useState(null);
  const [sel, setSel] = useState(new Set());
  const [ask, setAsk] = useState(false), [busy, setBusy] = useState(false);
  async function load() {
    try { const d = await api.get(`/api/outreach/followups?days=${days}`); setRows(d.followups || []); setSel(new Set((d.followups || []).map((r) => r.id))); }
    catch (e) { setRows([]); toast(e.message, 'error'); }
  }
  useEffect(() => { load(); }, [days]);
  async function send() {
    setBusy(true);
    try {
      const d = await api.post('/api/outreach/followups/send', { job_ids: [...sel], days: parseInt(days, 10) });
      if (d.error) throw new Error(d.error);
      toast(d.sent ? `Sent ${plural(d.sent, 'follow-up')}.` : 'No follow-ups were sent. Check the Gmail settings.', d.sent ? 'info' : 'error');
      setAsk(false); load();
    } catch (e) { toast(e.message, 'error'); }
    setBusy(false);
  }
  return html`<section className="section">
    <div className="section-head"><h2>Follow-ups due</h2><span className="count">${rows ? rows.length : ''}</span>
      <span className="why">no reply, at most 2 follow-ups each</span>
      <div className="section-actions">
        <label className="vh" for="fu-days">Emailed at least</label>
        <select id="fu-days" className="select" value=${days} onChange=${(e) => setDays(e.target.value)}>
          <option value="3">Emailed 3+ days ago</option><option value="5">Emailed 5+ days ago</option><option value="7">Emailed 7+ days ago</option></select>
        <button type="button" className="btn btn-primary btn-sm" disabled=${!sel.size} onClick=${() => setAsk(true)}>Send ${sel.size ? sel.size + ' ' : ''}follow-ups</button></div></div>
    ${rows === null ? html`<div className="skeleton-row"></div>`
    : !rows.length ? html`<${Empty} title="No follow-ups due.">Every company you emailed has replied, was followed up recently, or already had 2 follow-ups.</${Empty}>`
    : html`<ul className="ledger pick-list">${rows.map((j) => html`<li key=${j.id}><label className="pick">
        <input type="checkbox" className="check" checked=${sel.has(j.id)} onChange=${() => setSel((s) => { const n = new Set(s); n.has(j.id) ? n.delete(j.id) : n.add(j.id); return n; })} />
        <span className="pick-co"><b>${j.company}</b><span className="muted">${j.title || ''}</span></span>
        <span className="pick-mail">${j.followup_email}</span>
        <span className="muted">emailed ${ago(j.last_sent)}${(j.sends || 1) > 1 ? `, ${(j.sends || 1) - 1} follow-up sent` : ''}</span></label></li>`)}</ul>`}
    ${ask && html`<${Confirm} title=${`Send ${plural(sel.size, 'follow-up')}?`} busy=${busy}
      body="This sends your Follow-up template to the companies you picked." confirmLabel="Send follow-ups" onConfirm=${send} onClose=${() => setAsk(false)} />`}
  </section>`;
}

function SentLog({ refresh }) {
  const [rows, setRows] = useState(null);
  useEffect(() => { api.get('/api/outreach/log').then((d) => setRows(d.outreach || [])).catch(() => setRows([])); }, [refresh]);
  return html`<section className="section">
    <div className="section-head"><h2>Sent emails</h2><span className="why">latest 100</span></div>
    ${rows === null ? html`<div className="skeleton-row"></div>`
    : !rows.length ? html`<${Empty} title="No emails sent yet.">Emails you send from here, the Job board or Today show up in this list.</${Empty}>`
    : html`<div className="table-wrap"><table className="ledger-table is-static"><thead><tr><th className="c-co">Company</th><th>Sent to</th><th>Subject</th><th>Sent</th></tr></thead>
        <tbody>${rows.map((o, i) => html`<tr key=${i}><td className="c-co"><span className="co-name">${o.company || 'Unknown'}</span></td>
          <td>${o.hr_email || ''}</td><td className="c-role"><div>${o.subject || html`<span className="dash">Standard subject</span>`}</div></td>
          <td className="nowrap muted">${dateTime(o.sent_at)}</td></tr>`)}</tbody></table></div>`}
  </section>`;
}

export function Outreach({ user }) {
  const isAdmin = user && user.role === 'admin';
  const [settings, setSettings] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  async function load() {
    try {
      const [s, j] = await Promise.all([api.get('/api/settings'), api.get('/api/jobs')]);
      setSettings(s); setJobs(j.jobs || []); setError('');
    } catch (e) { setError(e.message); }
  }
  useEffect(() => { load(); }, [refresh]);
  return html`<div className="page">
    <header className="page-head"><h1>Email outreach</h1></header>
    ${error ? html`<${ErrorLine} message=${'Outreach didn’t load. ' + error} onRetry=${load} />`
    : !settings ? html`<div aria-busy="true">${[0, 1, 2].map((i) => html`<div key=${i} className="skeleton-row"></div>`)}</div>`
    : html`<${Followups} />
      <${Recipients} jobs=${jobs} template=${settings.outreach_template || ''} onSent=${() => setRefresh((n) => n + 1)} />
      <${Templates} settings=${settings} isAdmin=${isAdmin} onSaved=${(p) => setSettings((s) => ({ ...s, ...p }))} />
      <${SentLog} refresh=${refresh} />`}
  </div>`;
}
