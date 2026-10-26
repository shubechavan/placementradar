// Job search: run a scrape across job boards. Every job found is saved to the Job board.
import { html, useState, useEffect, useRef, api, toast, grade, phones, primaryEmail, telHref, mailHref, plural, shortDate, FEATURES } from '../lib.js';
import { Icon, Grade, Avatar, Empty } from '../ui.js';
import { siteName } from './board.js';

// Notes come from a test of every board on 26 Oct 2026 (two searches each).
const NONE = 'Returned no jobs in the last test.';
const GROUPS = [
  { title: 'Indian and general boards', sites: [
    ['indeed', 'Fastest source, with full job descriptions.'], ['linkedin', 'Slower, about 12 seconds.'],
    ['internshala'], ['naukri', 'Blocked by a captcha at the moment.'],
    ['shine', NONE], ['foundit', NONE], ['apna', NONE], ['glassdoor', NONE], ['zip_recruiter', 'US and Canada jobs only.'],
  ] },
  { title: 'Remote boards', sites: [['remoteok', 'Best for software roles.'], ['remotive', 'Few jobs per search.'],
    ['weworkremotely', NONE], ['remoteco', NONE], ['flexjobs', NONE]] },
  { title: 'Tech and startup boards', sites: [['himalayas'], ['hn_hiring', 'Monthly Hacker News thread; software roles.'],
    ['builtin', 'Shows the same jobs whatever you search.'], ['wellfound', NONE], ['yc_startup', NONE], ['turing', NONE]] },
  { title: 'Freelance and contract', sites: [['toptal', NONE], ['upwork', NONE], ['contra', NONE]] },
];
// Measured 26 Oct 2026: Indeed and LinkedIn returned 25 jobs per search; Naukri returned
// none (captcha) and Google Jobs is paused on the server, so the old default found jobs
// on Internshala alone.
const DEFAULT_SITES = ['indeed', 'linkedin', 'internshala'];
const OLD_DEFAULT = ['google_jobs', 'internshala', 'naukri'];
const remembered = () => {
  try {
    const s = JSON.parse(localStorage.getItem('pr.search') || 'null');
    // Anyone still on the old default gets the new one; a hand-picked set is kept.
    if (s && s.sites && [...s.sites].sort().join() === OLD_DEFAULT.join()) s.sites = DEFAULT_SITES;
    return s;
  } catch (e) { return null; }
};

export function JobSearch() {
  const saved = remembered();
  const [form, setForm] = useState(saved && saved.form || { term: 'software engineer', loc: 'India', count: '40', ploc: 'Mumbai, Bangalore, Pune', contacts: true, tech: true });
  const [sites, setSites] = useState(new Set(saved && saved.sites || DEFAULT_SITES));
  const [run, setRun] = useState(null);          // { id, progress, message, status }
  const [results, setResults] = useState(null);
  const [err, setErr] = useState('');
  const [recent, setRecent] = useState([]);
  const [customCount, setCustomCount] = useState(0);
  const timer = useRef(null);

  useEffect(() => {
    api.get('/api/dashboard').then((d) => setRecent(d.recent_searches || [])).catch(() => {});
    api.get('/api/custom-sites').then((d) => setCustomCount((d.sites || []).filter((s) => s.enabled !== false).length)).catch(() => {});
    return () => clearInterval(timer.current);
  }, []);
  useEffect(() => { try { localStorage.setItem('pr.search', JSON.stringify({ form, sites: [...sites] })); } catch (e) {} }, [form, sites]);

  const upd = (k) => (e) => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const toggle = (s) => setSites((x) => { const n = new Set(x); n.has(s) ? n.delete(s) : n.add(s); return n; });

  async function start(e) {
    e.preventDefault();
    if (!form.term.trim()) { setErr('Type a role or keywords to search for.'); return; }
    if (!sites.size) { setErr('Pick at least one job board.'); return; }
    setErr(''); setResults(null);
    try {
      const d = await api.post('/api/scrape', {
        search_term: form.term.trim(), location: form.loc.trim() || 'India', results_wanted: parseInt(form.count, 10),
        sites: [...sites], fetch_contacts: form.contacts, tech_only: form.tech, priority_locations: form.ploc,
      });
      setRun({ id: d.job_id, progress: 0, message: 'Starting…', status: 'running' });
      clearInterval(timer.current);
      timer.current = setInterval(() => poll(d.job_id), 1800);
    } catch (x) { setErr(`The search didn’t start. ${x.message}`); }
  }
  async function poll(id) {
    let s; try { s = await api.get(`/api/status/${id}`); } catch (e) { return; }
    setRun((r) => r && { ...r, progress: s.progress || 0, message: s.message || '', status: s.status });
    if (s.status === 'done' || s.status === 'error') {
      clearInterval(timer.current);
      if (s.status === 'error') { setErr(s.message || 'The search stopped with an error.'); setRun(null); return; }
      try { const r = await api.get(`/api/results/${id}`); setResults(r.results || []); } catch (e) { setResults([]); }
      const n = s.count || (s.stats && s.stats.total) || 0;
      toast(n ? `Found and saved ${plural(n, 'job')}.` : 'No jobs found. Try Google Jobs, Internshala and Naukri on their own.', n ? 'info' : 'error');
      setRun((r) => r && { ...r, status: 'done' });
    }
  }

  const running = run && run.status !== 'done';
  return html`<div className="page">
    <header className="page-head"><h1>Job search</h1></header>
    <form className="search-form" onSubmit=${start} noValidate>
      <div className="form-row">
        <div className="field grow"><label for="s-term">Role or keywords</label>
          <input id="s-term" className="input" value=${form.term} onInput=${upd('term')} placeholder="Data analyst, Java developer" /></div>
        <div className="field"><label for="s-loc">Location</label>
          <input id="s-loc" className="input" value=${form.loc} onInput=${upd('loc')} placeholder="India" /></div>
        <div className="field"><label for="s-count">How many jobs</label>
          <select id="s-count" className="select" value=${form.count} onChange=${upd('count')}>
            ${['20', '40', '75', '100', '150'].map((n) => html`<option key=${n} value=${n}>${n} jobs</option>`)}</select></div>
      </div>
      <div className="field"><label for="s-ploc">Cities to show first <span className="hint">separate with commas</span></label>
        <input id="s-ploc" className="input" value=${form.ploc} onInput=${upd('ploc')} placeholder="Mumbai, Pune" /></div>

      <div className="sources">
        ${GROUPS.map((g) => html`<fieldset key=${g.title} className="source-group">
          <legend>${g.title}</legend>
          ${g.sites.map(([s, note]) => html`<label key=${s} className="source">
            <input type="checkbox" className="check" checked=${sites.has(s)} onChange=${() => toggle(s)} />
            <span><span className="source-name">${siteName(s)}</span>${note && html`<span className="source-note">${note}</span>`}</span></label>`)}
        </fieldset>`)}
      </div>
      ${FEATURES.customSites && customCount > 0 && html`<p className="muted">Your ${plural(customCount, 'custom site')} from Custom sites ${customCount === 1 ? 'is' : 'are'} included automatically.</p>`}

      <div className="form-foot">
        <label className="inline-check"><input type="checkbox" className="check" checked=${form.contacts} onChange=${upd('contacts')} />Find phone numbers and emails for each company</label>
        <label className="inline-check"><input type="checkbox" className="check" checked=${form.tech} onChange=${upd('tech')} />Tech roles only (skips sales, BPO, accounts)</label>
        <button type="submit" className="btn btn-primary" disabled=${running}>${running ? 'Searching…' : 'Run search'}</button>
      </div>
      ${err && html`<p className="formerr" role="alert">${err}</p>`}
    </form>

    ${run && html`<div className="run" role="status" aria-live="polite">
      <div className="run-top"><b>${run.status === 'done' ? 'Search finished' : 'Searching'}</b><span className="muted">${run.message}</span><span className="num">${run.progress}%</span></div>
      <progress max="100" value=${run.progress} aria-label="Search progress"></progress>
      ${run.status === 'running' && html`<p className="muted small">Large searches run on GitHub Actions and can take a few minutes. You can leave this page; results are saved to the Job board.</p>`}
    </div>`}

    ${results && html`<section className="section">
      <div className="section-head"><h2>Results</h2><span className="count">${results.length}</span>
        <span className="why">saved to the Job board</span>
        <div className="section-actions">
          <a className="btn btn-sm" href="#/board">Open the Job board</a>
          ${run && html`<a className="btn btn-sm" href=${'/api/export?source=scrape&job_id=' + encodeURIComponent(run.id)} download><${Icon} name="download" size=${16} />Export CSV</a>`}
        </div></div>
      ${!results.length ? html`<${Empty} title="This search found no jobs.">Some boards block automated searches. Try Google Jobs, Internshala and Naukri on their own, or a broader role.</${Empty}>`
      : html`<div className="table-wrap" tabIndex="0" aria-label="Search results, scrolls sideways"><table className="ledger-table is-static"><thead><tr>
          <th className="c-co">Company</th><th>Grade</th><th>Phone</th><th>Email</th><th>Role</th><th>Source</th></tr></thead><tbody>
          ${results.map((r, i) => { const ph = phones(r)[0], em = primaryEmail(r); return html`<tr key=${r.job_url || i}>
            <td className="c-co"><div className="co-cell"><${Avatar} name=${r.company} /><span className="co-name">${r.company || 'Unnamed company'}</span></div></td>
            <td><${Grade} g=${grade(r)} /></td>
            <td className="c-phone">${ph ? (telHref(ph) ? html`<a href=${telHref(ph)}>${ph}</a>` : ph) : html`<span className="dash">None</span>`}</td>
            <td className="c-mail"><div>${em ? (mailHref(em) ? html`<a href=${mailHref(em)}>${em}</a>` : em) : html`<span className="dash">None</span>`}</div></td>
            <td className="c-role"><div>${r.title || ''}</div><div className="muted small">${r.location || ''}</div></td>
            <td className="muted">${siteName(r.site)}</td></tr>`; })}
        </tbody></table></div>`}
    </section>`}

    ${recent.length > 0 && !results && html`<section className="section">
      <div className="section-head"><h2>Recent searches</h2></div>
      <ul className="ledger">${recent.slice(0, 8).map((s, i) => html`<li key=${i} className="recent">
        <span><b>${s.search_term}</b> <span className="muted">in ${s.location || 'India'}</span></span>
        <span className="muted">${plural(s.job_count || 0, 'job')}</span>
        <span className="muted">${shortDate(s.created_at)}</span>
        <button type="button" className="btn btn-sm" onClick=${() => { setForm({ ...form, term: s.search_term || '', loc: s.location || '' }); window.scrollTo(0, 0); document.getElementById('s-term').focus(); }}>Use again</button>
      </li>`)}</ul></section>`}
  </div>`;
}
