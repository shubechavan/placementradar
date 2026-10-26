// Job board: every saved job, as a ledger. Click a row (or its company name) for details.
import {
  html, useState, useEffect, useMemo, useCallback, api, toast, grade, GRADE_ORDER, GRADE_HELP,
  STATUSES, statusLabel, phones, emails, primaryEmail, hasContact, telHref, mailHref, safeUrl,
  shortDate, dateTime, plural, copyText, OUTCOME_LABEL, CHANNEL_LABEL, FEATURES,
} from '../lib.js';
import { Icon, Grade, StatusSelect, Avatar, Panel, Confirm, LogOutcome, Empty, ErrorLine } from '../ui.js';

const PAGE = 50;
const SITE_NAMES = {
  google_jobs: 'Google Jobs', internshala: 'Internshala', naukri: 'Naukri', linkedin: 'LinkedIn',
  indeed: 'Indeed', glassdoor: 'Glassdoor', zip_recruiter: 'ZipRecruiter', shine: 'Shine', foundit: 'Foundit',
  apna: 'Apna', remotive: 'Remotive', remoteok: 'Remote OK', weworkremotely: 'We Work Remotely',
  remoteco: 'Remote.co', flexjobs: 'FlexJobs', himalayas: 'Himalayas', wellfound: 'Wellfound',
  yc_startup: 'YC Startups', builtin: 'Built In', hn_hiring: 'HN Hiring', turing: 'Turing',
  toptal: 'Toptal', upwork: 'Upwork', contra: 'Contra',
};
export const siteName = (s) => SITE_NAMES[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ') : '');
const CONTACT_FILTERS = [['', 'Any contact'], ['any', 'Has phone or email'], ['phone', 'Has phone'], ['email', 'Has email'], ['none', 'No contact yet']];

function csvOf(rows) {
  const cols = ['company', 'title', 'location', 'phone', 'emails', 'hr_contact', 'status', 'site', 'job_url', 'created_at'];
  const q = (v) => '"' + String(v ?? '').replace(/"/g, '""') + '"';
  return '﻿' + [cols.join(','), ...rows.map((r) => cols.map((c) => q(r[c])).join(','))].join('\r\n');
}

export function Board({ user, params, setParams }) {
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState('');
  const [sel, setSel] = useState(new Set());
  const [openId, setOpenId] = useState(null);
  const [logFor, setLogFor] = useState(null);
  const [ask, setAsk] = useState(null);          // confirm dialog state
  const [enrich, setEnrich] = useState(null);    // background "Find contacts" progress

  const q = params.q || '', status = params.status || '', site = params.site || '', contact = params.contact || '';
  const sort = params.sort || 'added', dir = params.dir || (sort === 'added' ? 'desc' : 'asc');
  const page = Math.max(1, parseInt(params.page || '1', 10) || 1);
  const set = (patch) => setParams({ ...params, page: '', ...patch });

  const load = useCallback(async () => {
    setError('');
    try { const d = await api.get('/api/jobs'); setJobs((d.jobs || []).map((j) => ({ ...j, _g: grade(j) }))); }
    catch (e) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const sites = useMemo(() => [...new Set((jobs || []).map((j) => j.site).filter(Boolean))].sort(), [jobs]);
  const filtered = useMemo(() => {
    if (!jobs) return [];
    const needle = q.trim().toLowerCase();
    let out = jobs.filter((r) => {
      if (status && (r.status || 'new') !== status) return false;
      if (site && r.site !== site) return false;
      const p = phones(r).length > 0, e = emails(r).length > 0;
      if (contact === 'any' && !(p || e)) return false;
      if (contact === 'phone' && !p) return false;
      if (contact === 'email' && !e) return false;
      if (contact === 'none' && (p || e)) return false;
      if (needle && !`${r.company} ${r.title} ${r.location} ${r.emails} ${r.phone}`.toLowerCase().includes(needle)) return false;
      return true;
    });
    const key = {
      company: (r) => (r.company || '').toLowerCase(),
      grade: (r) => GRADE_ORDER[r._g],
      status: (r) => STATUSES.findIndex(([k]) => k === (r.status || 'new')),
      added: (r) => r.created_at || '',
    }[sort] || ((r) => r.created_at || '');
    const m = dir === 'desc' ? -1 : 1;
    return [...out].sort((a, b) => (key(a) < key(b) ? -m : key(a) > key(b) ? m : 0));
  }, [jobs, q, status, site, contact, sort, dir]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const cur = Math.min(page, pages);
  const rows = filtered.slice((cur - 1) * PAGE, cur * PAGE);
  const selected = (jobs || []).filter((j) => sel.has(j.id));
  const open = (jobs || []).find((j) => j.id === openId);
  const noContactCount = (jobs || []).filter((j) => !hasContact(j)).length;

  function patchLocal(id, patch) { setJobs((js) => js.map((j) => (j.id === id ? { ...j, ...patch, _g: grade({ ...j, ...patch }) } : j))); }
  async function changeStatus(job, value) {
    const before = job.status;
    patchLocal(job.id, { status: value });
    try { await api.patch(`/api/jobs/${encodeURIComponent(job.id)}`, { status: value }); toast(`${job.company}: ${statusLabel(value)}`); }
    catch (e) { patchLocal(job.id, { status: before }); toast(`Status not saved. ${e.message}`, 'error'); }
  }
  function toggleSel(id) { setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  const allOnPage = rows.length > 0 && rows.every((r) => sel.has(r.id));
  function togglePage() { setSel((s) => { const n = new Set(s); rows.forEach((r) => (allOnPage ? n.delete(r.id) : n.add(r.id))); return n; }); }

  function sortBy(k) {
    if (sort === k) set({ dir: dir === 'asc' ? 'desc' : 'asc', sort: k });
    else set({ sort: k, dir: k === 'added' ? 'desc' : 'asc' });
  }
  const sortIcon = (k) => (sort === k ? (dir === 'asc' ? 'up' : 'down') : 'sort');
  const ariaSort = (k) => (sort === k ? (dir === 'asc' ? 'ascending' : 'descending') : 'none');

  // ── Actions ────────────────────────────────────────────────────────────
  async function startEnrich() {
    setAsk(null);
    setEnrich({ progress: 0, message: 'Starting…' });
    try {
      const d = await api.post('/api/jobs/enrich');
      const t = setInterval(async () => {
        let s; try { s = await api.get(`/api/status/${d.job_id}`); } catch (e) { return; }
        setEnrich({ progress: s.progress || 0, message: s.message || '' });
        if (s.status === 'done' || s.status === 'error') {
          clearInterval(t); setEnrich(null);
          toast(s.message || 'Finished finding contacts.', s.status === 'error' ? 'error' : 'info');
          load();
        }
      }, 2500);
    } catch (e) { setEnrich(null); toast(`Find contacts didn’t start. ${e.message}`, 'error'); }
  }

  async function sendEmails(list) {
    setAsk((a) => ({ ...a, busy: true }));
    try {
      const s = await api.get('/api/settings');
      if (!String(s.outreach_template || '').trim()) throw new Error('The Cold outreach template is empty. Add it in Settings first.');
      const recipients = list.map((j) => ({ email: primaryEmail(j), company: j.company, title: j.title, job_id: j.id }));
      let ok = 0, bad = 0;
      if (recipients.length === 1) {
        const j = list[0];
        // Subject and body are filled in by the server from the saved Cold outreach template.
        const d = await api.post('/api/outreach/send', {
          job_id: j.id, company: j.company, email: recipients[0].email, title: j.title, template: 'outreach_template',
        });
        d.success ? ok++ : bad++;
      } else {
        const d = await api.post('/api/outreach/bulk', { recipients, template: s.outreach_template });
        ok = (d.results || []).filter((r) => r.success).length; bad = (d.results || []).length - ok;
      }
      toast(bad ? `Sent ${ok}. ${bad} failed: check the Gmail settings.` : `Sent ${plural(ok, 'email')}.`, bad ? 'error' : 'info');
      setAsk(null); setSel(new Set()); load();
    } catch (e) { toast(e.message, 'error'); setAsk((a) => a && { ...a, busy: false }); }
  }
  function askEmail(list) {
    const withEmail = list.filter((j) => primaryEmail(j));
    const skipped = list.length - withEmail.length;
    if (!withEmail.length) { toast('None of these companies has an email address yet.', 'error'); return; }
    setAsk({
      title: withEmail.length === 1 ? `Email ${withEmail[0].company}?` : `Email ${plural(withEmail.length, 'company', 'companies')}?`,
      body: `This sends your Cold outreach template from the committee Gmail account${withEmail.length === 1 ? ` to ${primaryEmail(withEmail[0])}` : ''}.${skipped ? ` ${skipped} selected ${skipped === 1 ? 'company has' : 'companies have'} no email and will be skipped.` : ''}`,
      confirmLabel: withEmail.length === 1 ? 'Send email' : `Send ${plural(withEmail.length, 'email')}`,
      onConfirm: () => sendEmails(withEmail),
    });
  }
  async function copyAll(kind) {
    const items = [...new Set(selected.flatMap((j) => (kind === 'phone' ? phones(j) : emails(j))))];
    if (!items.length) { toast(`No ${kind === 'phone' ? 'phone numbers' : 'email addresses'} in the selection.`, 'error'); return; }
    const ok = await copyText(items.join('\n'));
    toast(ok ? `Copied ${plural(items.length, kind === 'phone' ? 'phone number' : 'email address', kind === 'phone' ? 'phone numbers' : 'email addresses')}.` : 'Copy failed. Your browser blocked the clipboard.', ok ? 'info' : 'error');
  }
  function exportSelected() {
    const blob = new Blob([csvOf(selected)], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `placementradar-selected-${selected.length}.csv`;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ── Render ─────────────────────────────────────────────────────────────
  const filtersOn = q || status || site || contact;
  return html`<div className="page">
    <header className="page-head">
      <h1>Job board</h1>
      <div className="page-actions">
        <button type="button" className="btn" disabled=${!!enrich || !jobs}
          onClick=${() => noContactCount
            ? setAsk({ title: 'Find contacts?', confirmLabel: `Find contacts for ${plural(noContactCount, 'job')}`, onConfirm: startEnrich,
                body: `${plural(noContactCount, 'job has', 'jobs have')} no phone or email. PlacementRadar will search company sites and business directories for them in the background (up to 300 at a time). This can take several minutes.` })
            : toast('Every job already has a phone or email.')}>
          <${Icon} name="search" size=${16} />${enrich ? `Finding contacts ${enrich.progress}%` : 'Find contacts'}</button>
        <a className="btn" href="/api/export?source=board" download><${Icon} name="download" size=${16} />Export CSV</a>
      </div>
    </header>
    ${enrich && html`<p className="progressline" role="status">${enrich.message}</p>`}

    ${sel.size ? html`<div className="bulkbar" role="region" aria-label="Selection actions">
        <b>${plural(sel.size, 'selected', 'selected')}</b>
        ${FEATURES.emailOutreach && html`<button type="button" className="btn btn-primary btn-sm" onClick=${() => askEmail(selected)}><${Icon} name="mail" size=${16} />Send email</button>`}
        <button type="button" className="btn btn-sm" onClick=${() => copyAll('phone')}>Copy phones</button>
        <button type="button" className="btn btn-sm" onClick=${() => copyAll('email')}>Copy emails</button>
        <button type="button" className="btn btn-sm" onClick=${exportSelected}>Export selected</button>
        <button type="button" className="btn btn-quiet btn-sm" onClick=${() => setSel(new Set())}>Clear selection</button>
      </div>`
    : html`<div className="toolbar" role="search">
        <div className="search-input"><${Icon} name="search" size=${16} />
          <label className="vh" for="bf-q">Search jobs</label>
          <input id="bf-q" className="input" type="search" placeholder="Search company, role, city, phone" value=${q}
            onInput=${(e) => set({ q: e.target.value })} /></div>
        <label className="vh" for="bf-status">Status</label>
        <select id="bf-status" className="select" value=${status} onChange=${(e) => set({ status: e.target.value })}>
          <option value="">All statuses</option>${STATUSES.map(([k, l]) => html`<option key=${k} value=${k}>${l}</option>`)}</select>
        <label className="vh" for="bf-site">Source</label>
        <select id="bf-site" className="select" value=${site} onChange=${(e) => set({ site: e.target.value })}>
          <option value="">All sources</option>${sites.map((s) => html`<option key=${s} value=${s}>${siteName(s)}</option>`)}</select>
        <label className="vh" for="bf-contact">Contact details</label>
        <select id="bf-contact" className="select" value=${contact} onChange=${(e) => set({ contact: e.target.value })}>
          ${CONTACT_FILTERS.map(([k, l]) => html`<option key=${k} value=${k}>${l}</option>`)}</select>
        ${filtersOn && html`<button type="button" className="btn btn-quiet btn-sm" onClick=${() => setParams({})}>Clear filters</button>`}
        <span className="tally" aria-live="polite">${jobs ? (filtersOn ? `${filtered.length.toLocaleString('en-IN')} of ${plural(jobs.length, 'job')}` : plural(jobs.length, 'job')) : ''}</span>
      </div>`}

    ${error ? html`<${ErrorLine} message=${'The job board didn’t load. ' + error} onRetry=${load} />`
    : !jobs ? html`<div aria-busy="true" aria-label="Loading jobs">${[0, 1, 2, 3, 4, 5].map((i) => html`<div key=${i} className="skeleton-row"></div>`)}</div>`
    : !jobs.length ? html`<${Empty} title="No saved jobs yet.">Run a search from Job search. Every job it finds is saved here.</${Empty}>`
    : !filtered.length ? html`<${Empty} title="No jobs match these filters.">Try a different status or source, or <button type="button" className="link-btn" onClick=${() => setParams({})}>clear all filters</button>.</${Empty}>`
    : html`<div className="table-wrap" tabIndex="0" aria-label="Jobs table, scrolls sideways">
      <table className="ledger-table">
        <thead><tr>
          <th className="c-co" aria-sort=${ariaSort('company')}><div className="co-cell">
            <input type="checkbox" className="check" checked=${allOnPage} onChange=${togglePage} aria-label="Select all jobs on this page" />
            <button type="button" className="sort" onClick=${() => sortBy('company')}>Company <${Icon} name=${sortIcon('company')} size=${14} /></button></div></th>
          <th aria-sort=${ariaSort('grade')}><button type="button" className="sort" onClick=${() => sortBy('grade')}>Grade <${Icon} name=${sortIcon('grade')} size=${14} /></button></th>
          <th>Phone</th><th>Email</th><th>Role</th>
          <th aria-sort=${ariaSort('status')}><button type="button" className="sort" onClick=${() => sortBy('status')}>Status <${Icon} name=${sortIcon('status')} size=${14} /></button></th>
          <th className="c-src">Source</th>
          <th className="c-date" aria-sort=${ariaSort('added')}><button type="button" className="sort" onClick=${() => sortBy('added')}>Added <${Icon} name=${sortIcon('added')} size=${14} /></button></th>
        </tr></thead>
        <tbody>${rows.map((r) => {
          const ph = phones(r), em = primaryEmail(r);
          return html`<tr key=${r.id} className=${(sel.has(r.id) ? 'is-selected ' : '') + (openId === r.id ? 'is-open' : '')} onClick=${() => setOpenId(r.id)}>
            <td className="c-co"><div className="co-cell">
              <input type="checkbox" className="check" checked=${sel.has(r.id)} onClick=${(e) => e.stopPropagation()} onChange=${() => toggleSel(r.id)} aria-label=${'Select ' + (r.company || 'job')} />
              <${Avatar} name=${r.company} />
              <span className="co-name"><button type="button" onClick=${(e) => { e.stopPropagation(); setOpenId(r.id); }}>${r.company || 'Unnamed company'}</button></span></div></td>
            <td><${Grade} g=${r._g} /></td>
            <td className="c-phone">${ph[0] ? (telHref(ph[0]) ? html`<a href=${telHref(ph[0])} onClick=${(e) => e.stopPropagation()}>${ph[0]}</a>` : ph[0]) : html`<span className="dash">None</span>`}
              ${ph.length > 1 && html`<div className="muted small">+${ph.length - 1} more</div>`}</td>
            <td className="c-mail"><div>${em ? (mailHref(em) ? html`<a href=${mailHref(em)} onClick=${(e) => e.stopPropagation()}>${em}</a>` : em) : html`<span className="dash">None</span>`}</div>
              ${emails(r).length > 1 && html`<div className="muted small">+${emails(r).length - 1} more</div>`}</td>
            <td className="c-role"><div title=${r.title}>${r.title || html`<span className="dash">Not listed</span>`}</div><div className="muted small">${r.location || ''}</div></td>
            <td><${StatusSelect} value=${r.status} label=${'Status for ' + (r.company || 'job')} onChange=${(v) => changeStatus(r, v)} /></td>
            <td className="c-src">${siteName(r.site)}</td>
            <td className="c-date" title=${dateTime(r.created_at)}>${shortDate(r.created_at)}</td>
          </tr>`; })}
        </tbody></table></div>
      <nav className="pager" aria-label="Pages">
        <span>Showing ${((cur - 1) * PAGE + 1).toLocaleString('en-IN')} to ${Math.min(cur * PAGE, filtered.length).toLocaleString('en-IN')} of ${filtered.length.toLocaleString('en-IN')}</span>
        ${pages > 1 && html`<span className="btns">
          <button type="button" className="btn btn-sm" disabled=${cur <= 1} onClick=${() => setParams({ ...params, page: String(cur - 1) })}>Previous page</button>
          <button type="button" className="btn btn-sm" disabled=${cur >= pages} onClick=${() => setParams({ ...params, page: String(cur + 1) })}>Next page</button></span>`}
      </nav>`}

    ${open && html`<${JobPanel} job=${open} user=${user} onClose=${() => setOpenId(null)}
      onStatus=${(v) => changeStatus(open, v)} onLog=${() => setLogFor(open)} onEmail=${() => askEmail([open])}
      onPatched=${(p) => patchLocal(open.id, p)} />`}
    ${logFor && html`<${LogOutcome} job=${logFor} user=${user} onClose=${() => setLogFor(null)}
      onSaved=${(o) => patchLocal(logFor.id, { status: { reply_received: 'replied', drive_scheduled: 'drive_scheduled', not_interested: 'not_interested' }[o] || 'contacted' })} />`}
    ${ask && html`<${Confirm} ...${ask} onClose=${() => setAsk(null)} />`}
  </div>`;
}

function JobPanel({ job, onClose, onStatus, onLog, onEmail, onPatched }) {
  const [acts, setActs] = useState(null);
  const [notes, setNotes] = useState(job.notes || '');
  const [savingNotes, setSavingNotes] = useState(false);
  useEffect(() => {
    setNotes(job.notes || ''); setActs(null);
    api.get(`/api/jobs/${encodeURIComponent(job.id)}/activities`).then((d) => setActs(d.activities || [])).catch(() => setActs([]));
  }, [job.id]);

  async function saveNotes() {
    setSavingNotes(true);
    try { await api.patch(`/api/jobs/${encodeURIComponent(job.id)}`, { notes }); onPatched({ notes }); toast('Notes saved.'); }
    catch (e) { toast(`Notes not saved. ${e.message}`, 'error'); }
    setSavingNotes(false);
  }
  async function saveScore(v) {
    try { await api.patch(`/api/jobs/${encodeURIComponent(job.id)}`, { score: parseInt(v, 10) }); onPatched({ score: parseInt(v, 10) }); }
    catch (e) { toast(`Rating not saved. ${e.message}`, 'error'); }
  }
  async function copy(v) { toast((await copyText(v)) ? `Copied ${v}` : 'Copy failed.', 'info'); }
  const ph = phones(job), em = emails(job), url = safeUrl(job.job_url), g = job._g || grade(job);

  return html`<${Panel} title=${job.company || 'Unnamed company'} onClose=${onClose} lead=${html`<${Grade} g=${g} />`}>
    <div className="panel-actions">
      ${ph[0] && telHref(ph[0]) && html`<a className="btn btn-primary" href=${telHref(ph[0])}><${Icon} name="phone" size=${16} />Call</a>`}
      <button type="button" className="btn" onClick=${onLog}>Log outcome</button>
      ${FEATURES.emailOutreach && primaryEmail(job) && html`<button type="button" className="btn" onClick=${onEmail}><${Icon} name="mail" size=${16} />Send email</button>`}
      ${url && html`<a className="btn btn-quiet" href=${url} target="_blank" rel="noopener noreferrer"><${Icon} name="external" size=${16} />Job posting</a>`}
    </div>
    <dl className="facts">
      <dt>Role</dt><dd>${job.title || 'Not listed'}</dd>
      <dt>Location</dt><dd>${job.location || 'Not listed'}</dd>
      <dt>Status</dt><dd><${StatusSelect} value=${job.status} onChange=${onStatus} /></dd>
      <dt>Phone</dt><dd>${ph.length ? ph.map((p) => html`<div key=${p} className="contact-line">
          ${telHref(p) ? html`<a href=${telHref(p)}>${p}</a>` : p}
          <button type="button" className="btn btn-quiet btn-sm btn-icon" onClick=${() => copy(p)} aria-label=${'Copy ' + p}><${Icon} name="copy" size=${15} /></button></div>`)
        : html`<span className="muted">None found</span>`}</dd>
      <dt>Email</dt><dd>${em.length ? em.map((e) => html`<div key=${e} className="contact-line">
          ${mailHref(e) ? html`<a href=${mailHref(e)}>${e}</a>` : e}${e === job.hr_contact && html`<span className="muted small"> HR contact</span>`}
          <button type="button" className="btn btn-quiet btn-sm btn-icon" onClick=${() => copy(e)} aria-label=${'Copy ' + e}><${Icon} name="copy" size=${15} /></button></div>`)
        : html`<span className="muted">None found</span>`}</dd>
      <dt>Grade</dt><dd>${g}: ${GRADE_HELP[g]}</dd>
      ${job.email_source && html`<dt>Found via</dt><dd>${String(job.email_source).replace(/[^\w .,&()-]/g, '').trim()}</dd>`}
      ${job.assigned_name && html`<dt>Owner</dt><dd>${job.assigned_name}</dd>`}
      <dt>Source</dt><dd>${siteName(job.site) || 'Unknown'}</dd>
      <dt>Added</dt><dd>${dateTime(job.created_at) || 'Unknown'}</dd>
      <dt><label for="jp-score">Rating</label></dt><dd>
        <select id="jp-score" className="select" value=${String(job.score || 0)} onChange=${(e) => saveScore(e.target.value)}>
          <option value="0">Not rated</option>${[1, 2, 3, 4, 5].map((n) => html`<option key=${n} value=${n}>${n} of 5</option>`)}</select></dd>
    </dl>
    <h3 className="sub-head"><label for="jp-notes">Notes</label></h3>
    <textarea id="jp-notes" className="textarea" value=${notes} onInput=${(e) => setNotes(e.target.value)} placeholder="Anything the next person calling should know"></textarea>
    <div style=${{ marginTop: 8 }}><button type="button" className="btn btn-sm" onClick=${saveNotes} disabled=${savingNotes || notes === (job.notes || '')}>${savingNotes ? 'Saving…' : 'Save notes'}</button></div>
    <h3 className="sub-head">Activity</h3>
    ${acts === null ? html`<p className="muted">Loading…</p>` : !acts.length ? html`<p className="muted">Nothing logged yet. Use Log outcome after you call or email.</p>`
      : html`<ul className="timeline">${acts.map((a, i) => html`<li key=${a.id || i}>
          <div className="t-top"><b>${a.user_name || 'Someone'}</b>
            <span>${(a.channels || [a.channel]).filter(Boolean).map((c) => CHANNEL_LABEL[c] || c).join(' and ')}</span>
            ${a.outcome && html`<span>${OUTCOME_LABEL[a.outcome] || a.outcome}</span>`}
            <time>${dateTime(a.created_at)}</time></div>${a.note && html`<p>${a.note}</p>`}</li>`)}</ul>`}
  </${Panel}>`;
}
