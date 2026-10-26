// HR database: the committee's own list of HR contacts. Grows with every search.
import { html, useState, useEffect, useMemo, api, toast, mailHref, plural } from '../lib.js';
import { Icon, Avatar, Dialog, Empty, ErrorLine } from '../ui.js';

const PAGE = 50;
const sourceLabel = (s) => {
  const t = String(s || '').trim();
  if (!t) return '';
  if (/^auto:/i.test(t)) return 'Found by a search';
  return t.replace(/[^\w .,&()-]/g, '').trim();
};
function downloadCsv(rows, name) {
  const q = (v) => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  const body = [['Company', 'HR email', 'HR name', 'Designation', 'City', 'Source'].join(','),
    ...rows.map((r) => [r.company, r.hr_email, r.hr_name, r.designation, r.city, r.source].map(q).join(','))].join('\r\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['﻿' + body], { type: 'text/csv;charset=utf-8' }));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
}

function AddContact({ onClose, onAdded }) {
  const [f, setF] = useState({ company: '', hr_email: '', hr_name: '', designation: '', city: '' });
  const [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const field = (k, label, opts = {}) => html`<div className="field">
    <label for=${'hr-' + k}>${label}${opts.optional && html` <span className="hint">optional</span>`}</label>
    <input id=${'hr-' + k} className="input" type=${opts.type || 'text'} value=${f[k]} placeholder=${opts.ph || ''}
      autocomplete="off" onInput=${(e) => setF({ ...f, [k]: e.target.value })} data-autofocus=${k === 'company' ? '' : null} /></div>`;
  async function save() {
    if (!f.company.trim() || !f.hr_email.trim()) { setErr('Company and HR email are both needed.'); return; }
    if (!mailHref(f.hr_email)) { setErr('That email address doesn’t look right. Check it and try again.'); return; }
    setBusy(true); setErr('');
    try {
      const d = await api.post('/api/hr-database/add', { ...f, designation: f.designation.trim() || 'HR' });
      if (!d.success) throw new Error('This email is already in the HR database.');
      toast(`Added ${f.hr_email} for ${f.company}.`); onAdded(); onClose();
    } catch (e) { setErr(e.message); setBusy(false); }
  }
  return html`<${Dialog} title="Add HR contact" onClose=${onClose} labelId="hr-add-title"
    footer=${html`<button type="button" className="btn" onClick=${onClose}>Cancel</button>
      <button type="button" className="btn btn-primary" onClick=${save} disabled=${busy}>${busy ? 'Adding…' : 'Add contact'}</button>`}>
    ${field('company', 'Company', { ph: 'Flipkart' })}
    ${field('hr_email', 'HR email', { type: 'email', ph: 'campus@company.com' })}
    ${field('hr_name', 'HR name', { optional: true })}
    ${field('designation', 'Designation', { optional: true, ph: 'Talent acquisition lead' })}
    ${field('city', 'City', { optional: true, ph: 'Mumbai' })}
    ${err && html`<p className="formerr" role="alert">${err}</p>`}
  </${Dialog}>`;
}

export function HrDatabase({ params, setParams }) {
  const [rows, setRows] = useState(null);
  const [stats, setStats] = useState({});
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const q = params.q || '', page = Math.max(1, parseInt(params.page || '1', 10) || 1);

  async function load() {
    setError('');
    try { const d = await api.get('/api/hr-database'); setRows(d.contacts || []); setStats(d.stats || {}); }
    catch (e) { setError(e.message); }
  }
  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const n = q.trim().toLowerCase();
    const out = n ? rows.filter((r) => `${r.company} ${r.hr_email} ${r.hr_name} ${r.city}`.toLowerCase().includes(n)) : rows;
    return [...out].sort((a, b) => String(a.company || '').localeCompare(String(b.company || '')));
  }, [rows, q]);
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE)), cur = Math.min(page, pages);
  const shown = filtered.slice((cur - 1) * PAGE, cur * PAGE);
  const autoCount = (rows || []).filter((r) => /^auto:/i.test(r.source || '')).length;

  return html`<div className="page">
    <header className="page-head">
      <h1>HR database</h1>
      <div className="page-actions">
        <button type="button" className="btn btn-primary" onClick=${() => setAdding(true)}>Add contact</button>
        <button type="button" className="btn" disabled=${!filtered.length} onClick=${() => downloadCsv(filtered, 'hr-contacts-kjsim.csv')}><${Icon} name="download" size=${16} />Export CSV</button>
      </div>
    </header>
    ${rows && html`<p className="stat-line">
      <span><b>${(stats.total_contacts ?? rows.length).toLocaleString('en-IN')}</b> contacts</span>
      <span><b>${(stats.total_companies ?? 0).toLocaleString('en-IN')}</b> companies</span>
      <span><b>${(stats.total_cities ?? 0).toLocaleString('en-IN')}</b> cities</span>
      <span><b>${autoCount.toLocaleString('en-IN')}</b> added automatically by searches</span></p>`}
    <div className="toolbar" role="search">
      <div className="search-input"><${Icon} name="search" size=${16} />
        <label className="vh" for="hr-q">Search the HR database</label>
        <input id="hr-q" className="input" type="search" placeholder="Search company, name, email, city" value=${q}
          onInput=${(e) => setParams({ q: e.target.value })} /></div>
      ${rows && q && html`<span className="tally">${filtered.length.toLocaleString('en-IN')} of ${plural(rows.length, 'contact')}</span>`}
    </div>
    ${error ? html`<${ErrorLine} message=${'The HR database didn’t load. ' + error} onRetry=${load} />`
    : !rows ? html`<div aria-busy="true">${[0, 1, 2, 3].map((i) => html`<div key=${i} className="skeleton-row"></div>`)}</div>`
    : !rows.length ? html`<${Empty} title="No HR contacts yet.">Add one with Add contact. Searches also add contacts here automatically when they find a named HR email.</${Empty}>`
    : !filtered.length ? html`<${Empty} title=${`Nothing matches “${q}”.`}>Check the spelling, or search by city or email domain instead.</${Empty}>`
    : html`<div className="table-wrap" tabIndex="0" aria-label="HR contacts, scrolls sideways">
        <table className="ledger-table is-static"><thead><tr>
          <th className="c-co">Company</th><th>Name</th><th>Designation</th><th>Email</th><th>City</th><th className="c-src">Source</th>
        </tr></thead><tbody>
        ${shown.map((r, i) => html`<tr key=${(r.hr_email || '') + i}>
          <td className="c-co"><div className="co-cell"><${Avatar} name=${r.company} /><span className="co-name">${r.company || 'Unnamed company'}</span></div></td>
          <td>${r.hr_name || ''}</td>
          <td className="muted">${r.designation || ''}</td>
          <td className="c-mail"><div>${mailHref(r.hr_email) ? html`<a href=${mailHref(r.hr_email)}>${r.hr_email}</a>` : r.hr_email}</div></td>
          <td>${r.city || ''}</td>
          <td className="c-src">${sourceLabel(r.source)}</td></tr>`)}
        </tbody></table></div>
      <nav className="pager" aria-label="Pages">
        <span>Showing ${((cur - 1) * PAGE + 1).toLocaleString('en-IN')} to ${Math.min(cur * PAGE, filtered.length).toLocaleString('en-IN')} of ${filtered.length.toLocaleString('en-IN')}</span>
        ${pages > 1 && html`<span className="btns">
          <button type="button" className="btn btn-sm" disabled=${cur <= 1} onClick=${() => setParams({ ...params, page: String(cur - 1) })}>Previous page</button>
          <button type="button" className="btn btn-sm" disabled=${cur >= pages} onClick=${() => setParams({ ...params, page: String(cur + 1) })}>Next page</button></span>`}
      </nav>`}
    ${adding && html`<${AddContact} onClose=${() => setAdding(false)} onAdded=${load} />`}
  </div>`;
}
