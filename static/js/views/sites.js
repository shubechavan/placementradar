// Custom sites: extra job websites that every search includes while they are switched on.
import { html, useState, useEffect, api, toast } from '../lib.js';
import { Dialog, Confirm, Empty, ErrorLine } from '../ui.js';
import { SITE_PRESETS, PRESET_LABELS } from './presets.js';

const SEL = [['card_sel', 'Job card', '.job-item, article.job-card'], ['title_sel', 'Title', 'h3.job-title'],
  ['company_sel', 'Company', '.company-name'], ['location_sel', 'Location', '.job-location'],
  ['salary_sel', 'Salary', '.salary'], ['link_sel', 'Link to the job', 'h3 > a']];
const EMPTY = { name: '', base_url: '', search_url: '', card_sel: '', title_sel: '', company_sel: '', location_sel: '', salary_sel: '', link_sel: '' };

function SiteDialog({ initial, onClose, onSaved }) {
  const [f, setF] = useState({ ...EMPTY, ...initial });
  const [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const [test, setTest] = useState(null);   // { state: 'running' | 'ok' | 'none' | 'error', text }
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const payload = () => Object.fromEntries(Object.entries(f).map(([k, v]) => [k, typeof v === 'string' ? v.trim() : v]));

  async function save() {
    const p = payload();
    if (!p.name || !p.search_url) { setErr('Site name and search address are both needed.'); return; }
    setBusy(true); setErr('');
    try {
      const d = await api.post('/api/custom-sites', { ...p, id: initial.id || undefined, enabled: initial.enabled !== false });
      if (d.success === false) throw new Error(d.error || 'The site wasn’t saved.');
      toast(`${p.name} saved. Your next search includes it.`); onSaved(); onClose();
    } catch (e) { setErr(e.message); setBusy(false); }
  }
  async function runTest() {
    setTest({ state: 'running', text: 'Fetching up to 3 jobs for “software engineer” in India…' });
    try {
      const d = await api.post('/api/custom-sites/test', { ...payload(), test_query: 'software engineer', test_location: 'India' });
      if (d.success && d.count > 0) setTest({ state: 'ok', text: `Found ${d.count} jobs, for example: ${(d.preview || []).map((j) => `${j.title} at ${j.company || 'unknown company'}`).join('; ')}.` });
      else if (d.success) setTest({ state: 'none', text: 'The site answered but no jobs were found. Check the search address, or fill in the CSS selectors.' });
      else setTest({ state: 'error', text: `The test failed: ${d.error || 'unknown error'}.` });
    } catch (e) { setTest({ state: 'error', text: `The test failed: ${e.message}` }); }
  }

  return html`<${Dialog} title=${initial.id ? `Edit ${initial.name}` : 'Add a job site'} onClose=${onClose} labelId="site-title"
    footer=${html`<button type="button" className="btn" onClick=${runTest} disabled=${!f.search_url || (test && test.state === 'running')}>Test the site</button>
      <span style=${{ flex: 1 }}></span>
      <button type="button" className="btn" onClick=${onClose}>Cancel</button>
      <button type="button" className="btn btn-primary" onClick=${save} disabled=${busy}>${busy ? 'Saving…' : 'Save site'}</button>`}>
    <div className="field"><label for="cs-name">Site name</label><input id="cs-name" className="input" value=${f.name} onInput=${set('name')} placeholder="TimesJobs" data-autofocus /></div>
    <div className="field"><label for="cs-base">Website address</label><input id="cs-base" className="input" type="url" value=${f.base_url} onInput=${set('base_url')} placeholder="https://www.timesjobs.com" /></div>
    <div className="field"><label for="cs-search">Search address</label>
      <input id="cs-search" className="input" type="url" value=${f.search_url} onInput=${set('search_url')} placeholder="https://site.com/jobs?q={query}&l={location}" />
      <span className="hint">Search the site in your browser, copy the address, then put {query} where the role is and {location} where the city is. {count} is also allowed.</span></div>
    <details className="advanced">
      <summary>CSS selectors <span className="hint">optional, improves accuracy</span></summary>
      <p className="hint" style=${{ margin: '8px 0 12px' }}>Leave these empty and the scraper finds job cards on its own. To set them, right-click a job on the site, choose Inspect and copy the class names.</p>
      <div className="sel-grid">${SEL.map(([k, l, ph]) => html`<div key=${k} className="field"><label for=${'cs-' + k}>${l}</label>
        <input id=${'cs-' + k} className="input" value=${f[k]} onInput=${set(k)} placeholder=${ph} /></div>`)}</div>
    </details>
    ${test && html`<p className=${'test-' + test.state} role="status">${test.text}</p>`}
    ${err && html`<p className="formerr" role="alert">${err}</p>`}
  </${Dialog}>`;
}

export function CustomSites() {
  const [sites, setSites] = useState(null);
  const [error, setError] = useState('');
  const [edit, setEdit] = useState(null);
  const [remove, setRemove] = useState(null);
  async function load() {
    try { const d = await api.get('/api/custom-sites'); setSites(d.sites || []); setError(''); } catch (e) { setError(e.message); }
  }
  useEffect(() => { load(); }, []);
  async function toggle(s) {
    const enabled = s.enabled === false;
    setSites((xs) => xs.map((x) => (x.id === s.id ? { ...x, enabled } : x)));
    try { await api.post(`/api/custom-sites/${encodeURIComponent(s.id)}/toggle`, { enabled }); toast(`${s.name} is ${enabled ? 'on' : 'off'}.`); }
    catch (e) { load(); toast(`Not changed. ${e.message}`, 'error'); }
  }
  async function addPreset(name) {
    try { await api.post('/api/custom-sites', { name, ...SITE_PRESETS[name], enabled: true }); toast(`${PRESET_LABELS[name] || name} added.`); load(); }
    catch (e) { toast(`Not added. ${e.message}`, 'error'); }
  }
  async function doRemove() {
    try { await api.del(`/api/custom-sites/${encodeURIComponent(remove.id)}`); toast(`${remove.name} removed.`); setRemove(null); load(); }
    catch (e) { toast(`Not removed. ${e.message}`, 'error'); }
  }
  const have = new Set((sites || []).map((s) => s.name));
  const presets = Object.keys(SITE_PRESETS).filter((n) => !have.has(n));

  return html`<div className="page">
    <header className="page-head"><h1>Custom sites</h1>
      <div className="page-actions"><button type="button" className="btn btn-primary" onClick=${() => setEdit({})}>Add a site</button></div></header>
    <p className="lede">Add any job website, no code needed. Every search includes the sites that are switched on.</p>
    ${error ? html`<${ErrorLine} message=${'Custom sites didn’t load. ' + error} onRetry=${load} />`
    : !sites ? html`<div className="skeleton-row"></div>`
    : !sites.length ? html`<${Empty} title="No custom sites yet.">Add one with Add a site, or pick a ready-made one below.</${Empty}>`
    : html`<ul className="ledger" style=${{ borderTop: '2px solid var(--ink)' }}>${sites.map((s) => html`<li key=${s.id} className="site-row">
        <div className="site-main"><b>${s.name}</b><span className="muted">${s.base_url || s.search_url}</span>
          <span className="muted small">${s.card_sel ? 'Uses your CSS selectors' : 'Finds job cards on its own'}</span></div>
        <label className="switch"><input type="checkbox" role="switch" checked=${s.enabled !== false} onChange=${() => toggle(s)} />
          <span className="switch-track" aria-hidden="true"></span><span>${s.enabled !== false ? 'On' : 'Off'}</span>
          <span className="vh"> for ${s.name}</span></label>
        <button type="button" className="btn btn-sm" onClick=${() => setEdit(s)}>Edit</button>
        <button type="button" className="btn btn-quiet btn-sm" onClick=${() => setRemove(s)}>Remove</button>
      </li>`)}</ul>`}
    ${presets.length > 0 && html`<section className="section">
      <div className="section-head"><h2>Ready-made sites</h2><span className="why">popular Indian boards, set up for you</span></div>
      <div className="preset-row">${presets.map((n) => html`<button key=${n} type="button" className="btn btn-sm" onClick=${() => addPreset(n)}>Add ${PRESET_LABELS[n] || n}</button>`)}</div>
    </section>`}
    ${edit && html`<${SiteDialog} initial=${edit} onClose=${() => setEdit(null)} onSaved=${load} />`}
    ${remove && html`<${Confirm} title=${`Remove ${remove.name}?`} body="Searches stop using this site. Jobs it already found stay on the Job board."
      confirmLabel="Remove site" onConfirm=${doRemove} onClose=${() => setRemove(null)} />`}
  </div>`;
}
