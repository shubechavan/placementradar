// Pipeline: companies by stage. Drag a card, or use "Move to…" (works by keyboard and on phones).
import {
  html, useState, useEffect, useRef, useMemo, api, toast, grade, GRADE_ORDER, phones, emails, primaryEmail,
  telHref, mailHref, safeUrl, daysSince, dateTime, plural, statusLabel, copyText, useIsPhone,
} from '../lib.js';
import { Icon, Grade, Avatar, Panel, LogOutcome, Empty, ErrorLine, MenuButton, ActivityList } from '../ui.js';

// Stage keys and stored statuses come from the API; labels match the rest of the app.
export const STAGES = [
  { key: 'to_contact', label: 'To contact' },
  { key: 'contacted', label: 'Contacted' },
  { key: 'interested', label: 'Replied' },          // stores status "replied"
  { key: 'drive_scheduled', label: 'Drive scheduled' },
  { key: 'closed_won', label: 'Placed', fold: true },
  { key: 'closed_lost', label: 'Closed', fold: true }, // rejected or not interested
];
const LABEL = Object.fromEntries(STAGES.map((s) => [s.key, s.label]));
const STALE_DAYS = 14;
const MUMBAI = /\b(mumbai|bombay|navi\s*mumbai|thane|andheri|powai|bandra|bkc|goregaon|malad|vikhroli|worli|lower\s*parel|vashi|airoli|belapur)\b/i;

function cityOf(c) {
  const l = String(c.location || '').trim();
  if (!l || l === 'nan') return '';
  if (/remote|work from home|\bwfh\b/i.test(l)) return 'Remote';
  if (MUMBAI.test(l)) return 'Mumbai';
  const first = l.split(',')[0].trim();
  if (!first || first.length > 24 || /^india$/i.test(first)) return '';
  return first.replace(/\b\w/g, (m) => m.toUpperCase());
}
function findCard(cols, id) {
  for (const k in cols) { const c = (cols[k] || []).find((x) => x.id === id); if (c) return [c, k]; }
  return [null, null];
}
const isStale = (c, stage) => (stage === 'to_contact' || stage === 'contacted') && (daysSince(c.created_at) ?? 0) >= STALE_DAYS;

function Card({ c, stage, open, dragging, onOpen, onMove, dnd }) {
  const ph = phones(c)[0], em = primaryEmail(c), g = grade(c), age = daysSince(c.created_at);
  return html`<li className=${'pcard' + (open ? ' is-open' : '') + (dragging ? ' is-dragging' : '')}
      draggable="true" onDragStart=${(e) => dnd.start(e, c.id, stage)} onDragEnd=${dnd.end}>
    <button type="button" className="pcard-main" onClick=${() => onOpen(c.id)}
      aria-label=${`${c.company || 'Company'}, ${LABEL[stage]}. Open details`}>
      <span className="pcard-co">${c.company || 'Unnamed company'}</span>
      <span className="pcard-role">${c.title || 'Role not listed'}</span>
    </button>
    <div className="pcard-meta">
      <${Grade} g=${g} />
      <span className="pcard-contact">${ph || em || 'No contact'}</span>
      ${c.assigned_name ? html`<${Avatar} name=${c.assigned_name} small title=${'Owner: ' + c.assigned_name} />` : null}
    </div>
    ${(isStale(c, stage) || stage === 'closed_lost') && html`<div className="pcard-foot-note">
      ${stage === 'closed_lost' ? html`<span className="muted small">${statusLabel(c.status)}</span>` : html`<span className="flag">Added ${age} days ago</span>`}
    </div>`}
    <div className="pcard-actions">
      ${ph && telHref(ph) && html`<a className="btn btn-quiet btn-sm" href=${telHref(ph)} aria-label=${'Call ' + (c.company || '')}><${Icon} name="phone" size=${15} />Call</a>`}
      <${MenuButton} className="btn btn-quiet btn-sm" ariaLabel=${'Move ' + (c.company || 'card') + ' to another stage'}
        items=${STAGES.map((s) => ({ value: s.key, label: s.label, disabled: s.key === stage, note: s.key === stage ? 'here now' : '' }))}
        onPick=${(to) => onMove(c.id, to)}>Move to…</${MenuButton}>
    </div>
  </li>`;
}

function Column({ st, cards, count, folded, onUnfold, onFold, footer, dropKey, over, dnd, openId, dragId, onOpen, onMove, hidden }) {
  if (folded) {
    return html`<div className="pcol is-folded" data-off=${hidden ? '1' : null}
        onDragOver=${(e) => dnd.over(e, dropKey)} onDragLeave=${(e) => dnd.leave(e, dropKey)} onDrop=${(e) => dnd.drop(e, st.key)}>
      <button type="button" className=${'pcol-fold' + (over === dropKey ? ' is-over' : '')} onClick=${onUnfold} aria-label=${`Show ${st.label}, ${count}`}>
        <span>${st.label}</span><span className="count">${count}</span></button></div>`;
  }
  return html`<section className="pcol" aria-label=${`${st.label}, ${count}`} data-off=${hidden ? '1' : null}>
    <div className="pcol-head"><h2>${st.label}</h2><span className="count">${count}</span>
      ${st.fold && html`<button type="button" className="btn btn-quiet btn-sm pcol-fold-btn" onClick=${onFold}>Fold</button>`}</div>
    <ul className=${'pcol-body' + (over === dropKey ? ' is-over' : '')}
      onDragOver=${(e) => dnd.over(e, dropKey)} onDragLeave=${(e) => dnd.leave(e, dropKey)} onDrop=${(e) => dnd.drop(e, st.key)}>
      ${cards.map((c) => html`<${Card} key=${c.id} c=${c} stage=${st.key} open=${openId === c.id} dragging=${dragId === c.id}
        onOpen=${onOpen} onMove=${onMove} dnd=${dnd} />`)}
      ${!cards.length && html`<li className="pcol-empty">Nothing here.</li>`}
    </ul>
    ${footer}
  </section>`;
}

export function Pipeline({ user, params, setParams }) {
  const isAdmin = user && user.role === 'admin';
  const [data, setData] = useState(null);       // { columns, counts }
  const [error, setError] = useState('');
  const [members, setMembers] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [logFor, setLogFor] = useState(null);
  const [refreshActs, setRefreshActs] = useState(0);
  const [unfolded, setUnfolded] = useState({});
  const [showUnassigned, setShowUnassigned] = useState(false);
  const [over, setOver] = useState(null);
  const [dragId, setDragId] = useState(null);
  const drag = useRef(null), seq = useRef(0);
  const phone = useIsPhone();

  const mine = params.mine === '1', q = params.q || '', owner = params.owner || '', city = params.city || '';
  const contact = params.contact || '', group = params.group || 'none', tab = params.stage || 'to_contact';
  const set = (p) => setParams({ ...params, ...p });

  async function load() {
    const my = ++seq.current;
    try {
      const d = await api.get(`/api/pipeline?mine=${mine ? 1 : 0}&search=${encodeURIComponent(q.trim())}`);
      if (my === seq.current) { setData(d); setError(''); }
    } catch (e) { if (my === seq.current) setError(e.message); }
  }
  useEffect(() => { const t = setTimeout(load, q ? 300 : 0); return () => clearTimeout(t); }, [mine, q]);
  useEffect(() => {
    if (isAdmin) api.get('/api/users').then((d) => setMembers((d.users || []).filter((u) => u.active !== false))).catch(() => {});
  }, [isAdmin]);

  const cols = (data && data.columns) || {};
  const all = STAGES.flatMap((s) => cols[s.key] || []);
  const owners = useMemo(() => [...new Set(all.map((c) => c.assigned_name).filter(Boolean))].sort(), [data]);
  const cities = useMemo(() => [...new Set(all.map(cityOf).filter(Boolean))].sort((a, b) => (a === 'Mumbai' ? -1 : b === 'Mumbai' ? 1 : a.localeCompare(b))), [data]);
  const passes = (c) => {
    if (owner === '__none' ? c.assigned_name : owner && c.assigned_name !== owner) return false;
    if (city && cityOf(c) !== city) return false;
    if (contact === 'phone' && !phones(c).length) return false;
    if (contact === 'email' && !emails(c).length) return false;
    if (contact === 'hr' && !String(c.hr_contact || '').trim()) return false;
    return true;
  };
  const narrowed = !!(owner || city || contact);
  function cardsFor(stage, extra = () => true) {
    let list = (cols[stage] || []).filter((c) => passes(c) && extra(c));
    if (stage === 'to_contact' && !mine && !showUnassigned && owner !== '__none' && group !== 'owner') list = list.filter((c) => c.assigned_to || c.assigned_name);
    if (stage === 'to_contact') list = [...list].sort((a, b) => GRADE_ORDER[grade(a)] - GRADE_ORDER[grade(b)]);
    return list;
  }

  async function move(id, to) {
    const [card, from] = findCard(cols, id);
    if (!card || from === to) return;
    const before = data;
    setData((d) => ({
      columns: { ...d.columns, [from]: d.columns[from].filter((x) => x.id !== id), [to]: [card, ...(d.columns[to] || [])] },
      counts: { ...d.counts, [from]: Math.max(0, (d.counts[from] || 1) - 1), [to]: (d.counts[to] || 0) + 1 },
    }));
    try {
      await api.post(`/api/jobs/${encodeURIComponent(id)}/stage`, { stage: to, company: card.company || '' });
      toast(`${card.company || 'Company'} moved to ${LABEL[to]}.`);
      load();
    } catch (e) { setData(before); toast(`Not moved. ${e.message}`, 'error'); }
  }
  async function assign(id, uid) {
    try {
      const d = await api.post(`/api/jobs/${encodeURIComponent(id)}/assign`, { user_id: uid });
      toast(uid ? `Owner set to ${d.assigned_name || 'member'}.` : 'Owner removed.'); load();
    } catch (e) { toast(`Owner not changed. ${e.message}`, 'error'); }
  }
  async function saveNotes(id, notes) {
    try {
      await api.post(`/api/jobs/${encodeURIComponent(id)}/remarks`, { remarks: notes });
      setData((d) => { const out = {}; for (const k in d.columns) out[k] = d.columns[k].map((x) => (x.id === id ? { ...x, notes } : x)); return { ...d, columns: out }; });
      toast('Notes saved.'); return true;
    } catch (e) { toast(`Notes not saved. ${e.message}`, 'error'); return false; }
  }

  const dnd = {
    start: (e, id, from) => { drag.current = { id, from }; setDragId(id); e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', id); } catch (_) {} },
    end: () => { drag.current = null; setDragId(null); setOver(null); },
    over: (e, key) => { if (!drag.current) return; e.preventDefault(); if (over !== key) setOver(key); },
    leave: (e, key) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver((k) => (k === key ? null : k)); },
    drop: (e, stage) => { e.preventDefault(); const d = drag.current; dnd.end(); if (d && d.from !== stage) move(d.id, stage); },
  };

  const countFor = (st) => (narrowed || (st.key === 'to_contact' && !mine && !showUnassigned) ? cardsFor(st.key).length : (data.counts || {})[st.key] || 0);
  const toContactFooter = !mine && html`<div className="pcol-foot">
    ${showUnassigned
      ? html`<button type="button" className="link-btn" onClick=${() => setShowUnassigned(false)}>Show only companies with an owner</button>`
      : html`<span className="muted">Only companies with an owner. </span><button type="button" className="link-btn" onClick=${() => setShowUnassigned(true)}>Show unassigned too</button>`}
    ${((data && data.counts && data.counts.to_contact) || 0) > ((cols.to_contact || []).length) && html`<p className="muted small" style=${{ marginTop: 6 }}>
      The newest ${(cols.to_contact || []).length} of ${(data.counts.to_contact || 0).toLocaleString('en-IN')} are loaded. The rest are on the <a href="#/board?status=new&contact=any">Job board</a>.</p>`}
  </div>`;

  function columns(laneKey, extra) {
    return STAGES.map((st) => {
      const folded = st.fold && !unfolded[st.key] && !phone;
      const cards = cardsFor(st.key, extra);
      return html`<${Column} key=${st.key} st=${st} cards=${cards} count=${extra ? cards.length : countFor(st)}
        folded=${folded} onUnfold=${() => setUnfolded((u) => ({ ...u, [st.key]: true }))} onFold=${() => setUnfolded((u) => ({ ...u, [st.key]: false }))}
        footer=${st.key === 'to_contact' && !extra ? toContactFooter : null}
        dropKey=${laneKey + '|' + st.key} over=${over} dnd=${dnd} openId=${openId} dragId=${dragId} onOpen=${setOpenId} onMove=${move}
        hidden=${tab !== st.key} />`;
    });
  }

  let body;
  if (error && !data) body = html`<${ErrorLine} message=${'The pipeline didn’t load. ' + error} onRetry=${load} />`;
  else if (!data) body = html`<div className="pboard" aria-busy="true" aria-label="Loading the pipeline">${[0, 1, 2, 3].map((i) => html`<div key=${i} className="pcol pcol-skel"></div>`)}</div>`;
  else if (!all.length) body = html`<${Empty} title=${q || mine ? 'No companies match.' : 'No companies in the pipeline yet.'}>${q || mine
    ? 'Try a different search, or switch to Everyone.'
    : 'A company appears here once a search finds a phone number or email for it. Run a search from Job search, or use Find contacts on the Job board.'}</${Empty}>`;
  else if (group === 'none') body = html`<div className="pboard" data-tab=${tab}>${columns('all')}</div>`;
  else {
    const keyOf = group === 'owner' ? (c) => c.assigned_name || 'No owner' : (c) => cityOf(c) || 'No city';
    const names = [...new Set(all.filter(passes).map(keyOf))].sort((a, b) => (/^No /.test(a) ? 1 : /^No /.test(b) ? -1 : a.localeCompare(b)));
    body = names.length ? names.map((n) => html`<section key=${n} className="plane" aria-label=${n}>
        <h2 className="plane-head">${group === 'owner' && html`<${Avatar} name=${n === 'No owner' ? '' : n} small />`}${n}
          <span className="count">${plural(all.filter((c) => passes(c) && keyOf(c) === n).length, 'company', 'companies')}</span></h2>
        <div className="pboard" data-tab=${tab}>${columns(n, (c) => keyOf(c) === n)}</div></section>`)
      : html`<${Empty} title="No companies match these filters."><button type="button" className="link-btn" onClick=${() => set({ owner: '', city: '', contact: '' })}>Clear filters</button></${Empty}>`;
  }

  const [openCard, openStage] = openId ? findCard(cols, openId) : [null, null];

  return html`<div className="page page-wide">
    <header className="page-head">
      <h1>Pipeline</h1>
      <div className="page-actions">
        <div className="seg" role="group" aria-label="Whose companies">
          <button type="button" aria-pressed=${mine} onClick=${() => set({ mine: '1' })}>Mine</button>
          <button type="button" aria-pressed=${!mine} onClick=${() => set({ mine: '' })}>Everyone</button>
        </div>
        <button type="button" className="btn btn-icon" onClick=${load} aria-label="Refresh the pipeline" title="Refresh"><${Icon} name="refresh" /></button>
      </div>
    </header>
    <div className="toolbar" role="search">
      <div className="search-input"><${Icon} name="search" size=${16} />
        <label className="vh" for="pf-q">Search the pipeline</label>
        <input id="pf-q" className="input" type="search" placeholder="Search company, role, city" value=${q} onInput=${(e) => set({ q: e.target.value })} /></div>
      <label className="vh" for="pf-owner">Owner</label>
      <select id="pf-owner" className="select" value=${owner} onChange=${(e) => set({ owner: e.target.value })}>
        <option value="">Any owner</option><option value="__none">No owner</option>
        ${owners.map((o) => html`<option key=${o} value=${o}>${o}</option>`)}</select>
      <label className="vh" for="pf-city">City</label>
      <select id="pf-city" className="select" value=${city} onChange=${(e) => set({ city: e.target.value })}>
        <option value="">Any city</option>${cities.map((c) => html`<option key=${c} value=${c}>${c}</option>`)}</select>
      <label className="vh" for="pf-contact">Contact details</label>
      <select id="pf-contact" className="select" value=${contact} onChange=${(e) => set({ contact: e.target.value })}>
        <option value="">Any contact</option><option value="phone">Has phone</option><option value="email">Has email</option><option value="hr">Named HR contact</option></select>
      <label className="for-desk label" for="pf-group">Group by</label>
      <select id="pf-group" className="select for-desk" value=${group} onChange=${(e) => set({ group: e.target.value === 'none' ? '' : e.target.value })}>
        <option value="none">No grouping</option><option value="owner">Owner</option><option value="city">City</option></select>
      ${narrowed && html`<button type="button" className="btn btn-quiet btn-sm" onClick=${() => set({ owner: '', city: '', contact: '' })}>Clear filters</button>`}
    </div>
    ${data && all.length > 0 && html`<div className="stage-tabs" role="tablist" aria-label="Stage">
      ${STAGES.map((st) => html`<button key=${st.key} type="button" role="tab" aria-selected=${tab === st.key}
        onClick=${() => set({ stage: st.key === 'to_contact' ? '' : st.key })}>${st.label} <span className="count">${countFor(st)}</span></button>`)}
    </div>`}
    ${body}

    ${openCard && html`<${CardPanel} c=${openCard} stage=${openStage} isAdmin=${isAdmin} members=${members} refresh=${refreshActs}
      onClose=${() => setOpenId(null)} onMove=${move} onAssign=${assign} onSaveNotes=${saveNotes} onLog=${() => setLogFor(openCard)} />`}
    ${logFor && html`<${LogOutcome} job=${logFor} user=${user} onClose=${() => setLogFor(null)} onSaved=${() => { setRefreshActs((n) => n + 1); load(); }} />`}
  </div>`;
}

function CardPanel({ c, stage, isAdmin, members, refresh, onClose, onMove, onAssign, onSaveNotes, onLog }) {
  const [notes, setNotes] = useState(c.notes || '');
  const [saving, setSaving] = useState(false);
  useEffect(() => { setNotes(c.notes || ''); }, [c.id]);
  const ph = phones(c), em = emails(c), site = safeUrl(c.company_url), g = grade(c);
  const ownerId = c.assigned_to || ((members.find((m) => (m.name || m.email) === c.assigned_name) || {}).id) || '';
  async function copy(v) { toast((await copyText(v)) ? `Copied ${v}` : 'Copy failed.'); }

  return html`<${Panel} title=${c.company || 'Unnamed company'} onClose=${onClose} lead=${html`<${Grade} g=${g} />`}>
    <div className="panel-actions">
      ${ph[0] && telHref(ph[0]) && html`<a className="btn btn-primary" href=${telHref(ph[0])}><${Icon} name="phone" size=${16} />Call</a>`}
      <button type="button" className="btn" onClick=${onLog}>Log outcome</button>
      <${MenuButton} className="btn" items=${STAGES.map((s) => ({ value: s.key, label: s.label, disabled: s.key === stage, note: s.key === stage ? 'here now' : '' }))}
        onPick=${(to) => onMove(c.id, to)}>Move to…</${MenuButton}>
    </div>
    <dl className="facts">
      <dt>Stage</dt><dd>${LABEL[stage]}${stage === 'closed_lost' ? ` (${statusLabel(c.status)})` : ''}</dd>
      <dt>${isAdmin ? html`<label for="cp-owner">Owner</label>` : 'Owner'}</dt><dd>${isAdmin
        ? html`<select id="cp-owner" className="select" value=${ownerId} onChange=${(e) => onAssign(c.id, e.target.value)}>
            <option value="">No owner</option>${members.map((m) => html`<option key=${m.id} value=${m.id}>${m.name || m.email}</option>`)}</select>`
        : c.assigned_name || html`<span className="muted">No owner</span>`}</dd>
      <dt>Role</dt><dd>${c.title || 'Not listed'}</dd>
      <dt>Location</dt><dd>${c.location || 'Not listed'}</dd>
      <dt>Phone</dt><dd>${ph.length ? ph.map((p) => html`<div key=${p} className="contact-line">${telHref(p) ? html`<a href=${telHref(p)}>${p}</a>` : p}
          <button type="button" className="btn btn-quiet btn-sm btn-icon" onClick=${() => copy(p)} aria-label=${'Copy ' + p}><${Icon} name="copy" size=${15} /></button></div>`)
        : html`<span className="muted">None found</span>`}</dd>
      <dt>Email</dt><dd>${em.length ? em.map((e) => html`<div key=${e} className="contact-line">${mailHref(e) ? html`<a href=${mailHref(e)}>${e}</a>` : e}
          <button type="button" className="btn btn-quiet btn-sm btn-icon" onClick=${() => copy(e)} aria-label=${'Copy ' + e}><${Icon} name="copy" size=${15} /></button></div>`)
        : html`<span className="muted">None found</span>`}</dd>
      ${c.hr_contact && html`<dt>HR contact</dt><dd>${c.hr_contact}</dd>`}
      ${site && html`<dt>Website</dt><dd><a href=${site} target="_blank" rel="noopener noreferrer">${site.replace(/^https?:\/\//, '')}</a></dd>`}
      <dt>Added</dt><dd>${dateTime(c.created_at) || 'Unknown'}</dd>
    </dl>
    <h3 className="sub-head"><label for="cp-notes">Notes</label></h3>
    <textarea id="cp-notes" className="textarea" maxLength="2000" value=${notes} onInput=${(e) => setNotes(e.target.value)} placeholder="For example: HR asked us to call after Diwali"></textarea>
    <div style=${{ marginTop: 8 }}><button type="button" className="btn btn-sm" disabled=${saving || notes === (c.notes || '')}
      onClick=${async () => { setSaving(true); await onSaveNotes(c.id, notes); setSaving(false); }}>${saving ? 'Saving…' : 'Save notes'}</button></div>
    <h3 className="sub-head">Activity</h3>
    <${ActivityList} jobId=${c.id} refresh=${refresh} />
  </${Panel}>`;
}
