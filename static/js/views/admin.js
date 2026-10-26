// Admin views: Leaderboard and Members.
import { html, useState, useEffect, api, toast, shortDate, mailHref } from '../lib.js';
import { Icon, Avatar, Dialog, Confirm, Empty, ErrorLine } from '../ui.js';

const LB_COLS = [['score', 'Score'], ['companies', 'Companies'], ['calls', 'Calls'], ['emails', 'Emails'], ['whatsapps', 'WhatsApp'], ['replies', 'Replies'], ['drives', 'Drives']];

export function Leaderboard({ params, setParams }) {
  const range = params.range === 'all' ? 'all' : 'week';
  const sort = LB_COLS.some(([k]) => k === params.sort) ? params.sort : 'score';
  const [d, setD] = useState(null), [error, setError] = useState('');
  async function load() {
    setD(null);
    try { setD(await api.get(`/api/leaderboard?range=${range}`)); setError(''); } catch (e) { setError(e.message); }
  }
  useEffect(() => { load(); }, [range]);
  const rows = d ? [...(d.board || [])].sort((a, b) => (b[sort] || 0) - (a[sort] || 0)) : [];
  const w = (d && d.weights) || {};
  return html`<div className="page">
    <header className="page-head"><h1>Leaderboard</h1>
      <div className="page-actions"><div className="seg" role="group" aria-label="Time range">
        <button type="button" aria-pressed=${range === 'week'} onClick=${() => setParams({ ...params, range: '' })}>Last 7 days</button>
        <button type="button" aria-pressed=${range === 'all'} onClick=${() => setParams({ ...params, range: 'all' })}>All time</button></div></div></header>
    <p className="lede">Results count more than effort. A reply or a scheduled drive earns ${w.reply_received || 5} points,
      an interested company ${w.interested || 3}, each new company contacted ${w.company || 2}, and each call, email or WhatsApp 1.</p>
    ${error ? html`<${ErrorLine} message=${'The leaderboard didn’t load. ' + error} onRetry=${load} />`
    : !d ? html`<div className="skeleton-row"></div>`
    : !rows.length ? html`<${Empty} title="No activity yet.">Points appear once members log outcomes from Today, the Job board or the Pipeline.</${Empty}>`
    : html`<div className="table-wrap"><table className="ledger-table is-static"><thead><tr>
        <th className="c-co">Member <span className="muted">by rank</span></th>
        ${LB_COLS.map(([k, l]) => html`<th key=${k} className="num-col" aria-sort=${sort === k ? 'descending' : 'none'}>
          <button type="button" className="sort" onClick=${() => setParams({ ...params, sort: k === 'score' ? '' : k })}>${l}${sort === k && html` <${Icon} name="down" size=${14} />`}</button></th>`)}
      </tr></thead><tbody>${rows.map((r, i) => html`<tr key=${r.user_id || r.user_name}>
        <td className="c-co"><div className="co-cell"><span className="rank num" aria-label=${'Rank ' + (i + 1)}>${i + 1}</span><${Avatar} name=${r.user_name} /><span className="co-name">${r.user_name || 'Unknown'}</span></div></td>
        ${LB_COLS.map(([k]) => html`<td key=${k} className=${'num-col num' + (k === 'score' ? ' strong' : '')}>${r[k] || 0}</td>`)}</tr>`)}
      </tbody></table></div>`}
  </div>`;
}

function AddMember({ onClose, onAdded }) {
  const [f, setF] = useState({ name: '', email: '', password: '', role: 'member' });
  const [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  async function save() {
    if (!f.email.trim()) { setErr('An email address is needed.'); return; }
    if (f.password.length < 8) { setErr('The password needs at least 8 characters.'); return; }
    setBusy(true); setErr('');
    try { await api.post('/api/users', f); toast(`${f.name || f.email} added. Share the password with them directly.`); onAdded(); onClose(); }
    catch (e) { setErr(e.message); setBusy(false); }
  }
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return html`<${Dialog} title="Add a committee member" onClose=${onClose} labelId="mem-title"
    footer=${html`<button type="button" className="btn" onClick=${onClose}>Cancel</button>
      <button type="button" className="btn btn-primary" onClick=${save} disabled=${busy}>${busy ? 'Adding…' : 'Add member'}</button>`}>
    <div className="field"><label for="mem-name">Name</label><input id="mem-name" className="input" value=${f.name} onInput=${set('name')} autocomplete="off" data-autofocus /></div>
    <div className="field"><label for="mem-email">Email</label><input id="mem-email" className="input" type="email" value=${f.email} onInput=${set('email')} autocomplete="off" /></div>
    <div className="field"><label for="mem-pass">Starting password</label><input id="mem-pass" className="input" type="password" value=${f.password} onInput=${set('password')} autocomplete="new-password" />
      <span className="hint">At least 8 characters.</span></div>
    <fieldset style=${{ border: 0, padding: 0, margin: 0 }}><legend className="label" style=${{ marginBottom: 8 }}>Role</legend>
      <div className="choice-row">
        <label className="choice"><input type="radio" name="role" checked=${f.role === 'member'} onChange=${() => setF({ ...f, role: 'member' })} />Member</label>
        <label className="choice"><input type="radio" name="role" checked=${f.role === 'admin'} onChange=${() => setF({ ...f, role: 'admin' })} />Admin</label></div>
      <span className="hint" style=${{ display: 'block', marginTop: 6 }}>Admins can change settings, assign companies and manage members.</span></fieldset>
    ${err && html`<p className="formerr" role="alert">${err}</p>`}
  </${Dialog}>`;
}

export function Members({ user }) {
  const [rows, setRows] = useState(null), [error, setError] = useState('');
  const [adding, setAdding] = useState(false), [remove, setRemove] = useState(null);
  async function load() { try { const d = await api.get('/api/users'); setRows(d.users || []); setError(''); } catch (e) { setError(e.message); } }
  useEffect(() => { load(); }, []);
  async function setActive(u, active) {
    try { await api.post(`/api/users/${encodeURIComponent(u.id)}/active`, { active }); toast(`${u.name || u.email} can ${active ? 'now' : 'no longer'} sign in.`); load(); }
    catch (e) { toast(e.message, 'error'); }
  }
  async function doRemove() {
    try { await api.del(`/api/users/${encodeURIComponent(remove.id)}`); toast(`${remove.name || remove.email} removed.`); setRemove(null); load(); }
    catch (e) { toast(e.message, 'error'); }
  }
  return html`<div className="page">
    <header className="page-head"><h1>Members</h1>
      <div className="page-actions"><button type="button" className="btn btn-primary" onClick=${() => setAdding(true)}>Add member</button></div></header>
    ${error ? html`<${ErrorLine} message=${'Members didn’t load. ' + error} onRetry=${load} />`
    : !rows ? html`<div className="skeleton-row"></div>`
    : !rows.length ? html`<${Empty} title="No members yet.">Add the committee with Add member.</${Empty}>`
    : html`<div className="table-wrap"><table className="ledger-table is-static"><thead><tr>
        <th className="c-co">Name</th><th>Email</th><th>Role</th><th>Sign-in</th><th>Added</th><th><span className="vh">Actions</span></th></tr></thead>
      <tbody>${rows.map((u) => { const me = user && u.email === user.email; const on = u.active !== false; return html`<tr key=${u.id}>
        <td className="c-co"><div className="co-cell"><${Avatar} name=${u.name || u.email} /><span className="co-name">${u.name || 'No name'}${me ? ' (you)' : ''}</span></div></td>
        <td>${mailHref(u.email) ? html`<a href=${mailHref(u.email)}>${u.email}</a>` : u.email}</td>
        <td>${u.role === 'admin' ? 'Admin' : 'Member'}</td>
        <td><span className=${'status ' + (on ? 'st-placed' : 'st-new')}><span className="swatch" aria-hidden="true"></span>${on ? 'Allowed' : 'Turned off'}</span></td>
        <td className="muted nowrap">${shortDate(u.created_at)}</td>
        <td className="nowrap">${me ? html`<span className="muted small">This is you</span>` : html`
          <button type="button" className="btn btn-sm" onClick=${() => setActive(u, !on)}>${on ? 'Turn off sign-in' : 'Allow sign-in'}</button>
          <button type="button" className="btn btn-quiet btn-sm" onClick=${() => setRemove(u)}>Remove</button>`}</td></tr>`; })}
      </tbody></table></div>`}
    ${adding && html`<${AddMember} onClose=${() => setAdding(false)} onAdded=${load} />`}
    ${remove && html`<${Confirm} title=${`Remove ${remove.name || remove.email}?`}
      body="They can no longer sign in, and they drop off the leaderboard. Companies they own keep their owner name until someone reassigns them. To pause someone instead, use Turn off sign-in."
      confirmLabel="Remove member" onConfirm=${doRemove} onClose=${() => setRemove(null)} />`}
  </div>`;
}
