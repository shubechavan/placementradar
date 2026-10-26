// Today: the call sheet. Three ruled lists, each row ends in an action.
import {
  html, useState, useEffect, useCallback, api, toast, grade, GRADE_ORDER, phones, primaryEmail,
  mailHref, daysSince, ago, plural, loggedToday, FEATURES,
} from '../lib.js';
import { Icon, Grade, Status, Avatar, PhoneLinks, LogOutcome, Confirm, Empty, ErrorLine } from '../ui.js';

const STALE_DAYS = 14;      // same threshold the pipeline uses to flag a card
const FOLLOWUP_DAYS = 3;
const PAGE = 8;

function useToday() {
  const [state, setState] = useState({ loading: true });
  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: '' }));
    try {
      const [fu, mine] = await Promise.all([
        FEATURES.emailOutreach ? api.get(`/api/outreach/followups?days=${FOLLOWUP_DAYS}`) : Promise.resolve({ followups: [] }),
        api.get('/api/pipeline?mine=1'),
      ]);
      let pipe = mine, scope = 'mine';
      const mineCount = Object.values(mine.columns || {}).reduce((n, c) => n + c.length, 0);
      if (!mineCount) { pipe = await api.get('/api/pipeline'); scope = 'everyone'; }
      const cols = pipe.columns || {};
      const followups = fu.followups || [];
      const fuIds = new Set(followups.map((f) => f.id));
      const callNext = (cols.to_contact || [])
        .filter((c) => phones(c).length && (scope === 'mine' || !c.assigned_to))
        .map((c) => ({ ...c, _g: grade(c) }))
        .sort((a, b) => GRADE_ORDER[a._g] - GRADE_ORDER[b._g]);
      const stale = (cols.contacted || [])
        .filter((c) => !fuIds.has(c.id) && (daysSince(c.created_at) ?? 0) >= STALE_DAYS)
        .sort((a, b) => (daysSince(b.created_at) ?? 0) - (daysSince(a.created_at) ?? 0));
      setState({ loading: false, followups, callNext, stale, scope, toContactTotal: (pipe.counts || {}).to_contact || 0 });
    } catch (e) {
      setState({ loading: false, error: e.message });
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  return [state, load, setState];
}

function SectionHead({ title, count, why, children }) {
  return html`<div className="section-head">
    <h2>${title}</h2>${count !== undefined && html`<span className="count">${count}</span>`}
    ${why && html`<span className="why">${why}</span>`}
    ${children && html`<div className="section-actions">${children}</div>`}
  </div>`;
}

function Entry({ row, leaving, lead, contact, when, onLog }) {
  return html`<li className=${'entry' + (leaving ? ' is-leaving' : '')}>
    ${lead}
    <div className="co"><b>${row.company || 'Unnamed company'}</b><span>${row.title || 'Role not listed'}</span></div>
    <div className="contact">${contact}</div>
    <div className="when">${when}</div>
    <div className="acts">
      ${phones(row)[0] && html`<a className="btn btn-sm" href=${'tel:' + phones(row)[0].replace(/[^0-9+]/g, '')}><${Icon} name="phone" size=${16} />Call</a>`}
      <button type="button" className="btn btn-sm" onClick=${() => onLog(row)}>Log outcome</button>
    </div>
  </li>`;
}

export function Today({ user }) {
  const [s, reload, setS] = useToday();
  const [logFor, setLogFor] = useState(null);
  const [leaving, setLeaving] = useState(new Set());
  const [shown, setShown] = useState(PAGE);
  const [confirmFU, setConfirmFU] = useState(false);
  const [sending, setSending] = useState(false);
  const [logged, setLogged] = useState(loggedToday(user && user.email));

  // Logging an outcome takes the row off the sheet: it folds away, then leaves the list.
  function removeAfterLog(id) {
    setLogged(loggedToday(user && user.email));
    setLeaving((x) => new Set(x).add(id));
    setTimeout(() => {
      setS((st) => ({
        ...st,
        followups: st.followups.filter((r) => r.id !== id),
        callNext: st.callNext.filter((r) => r.id !== id),
        stale: st.stale.filter((r) => r.id !== id),
      }));
    }, 300);
  }

  async function sendFollowups() {
    setSending(true);
    try {
      const d = await api.post('/api/outreach/followups/send', { days: FOLLOWUP_DAYS });
      if (d.error) throw new Error(d.error);
      const ok = (d.results || []).filter((r) => r.success).length;
      const bad = (d.results || []).length - ok;
      toast(bad ? `Sent ${ok} follow-ups. ${bad} failed: check the Gmail settings.` : `Sent ${plural(ok, 'follow-up')}.`, bad ? 'error' : 'info');
      setConfirmFU(false); reload();
    } catch (e) { toast(e.message, 'error'); }
    setSending(false);
  }

  const today = new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' });

  return html`<div className="page">
    <header className="page-head">
      <h1>Today</h1>
      <div className="head-meta">
        <span className="muted">${today}</span>
        ${logged > 0 && html`<span>You logged ${plural(logged, 'outcome')} today</span>`}
      </div>
    </header>

    ${s.error ? html`<${ErrorLine} message=${'Today’s list didn’t load. ' + s.error} onRetry=${reload} />` : s.loading && !s.callNext
      ? html`<div aria-busy="true" aria-label="Loading today’s list">${[0, 1, 2, 3].map((i) => html`<div key=${i} className="skeleton-row"></div>`)}</div>`
      : html`
    ${FEATURES.emailOutreach && html`<section className="section" aria-labelledby="fu-h">
      <${SectionHead} title=${html`<span id="fu-h">Follow-ups due</span>`} count=${s.followups.length}
        why=${s.followups.length ? `emailed ${FOLLOWUP_DAYS}+ days ago, no reply yet` : null}>
        ${s.followups.length > 0 && html`<button type="button" className="btn btn-primary btn-sm" onClick=${() => setConfirmFU(true)}>Send follow-ups</button>`}
      </${SectionHead}>
      ${s.followups.length ? html`<ul className="ledger">${s.followups.map((r) => html`<${Entry} key=${r.id} row=${r}
          leaving=${leaving.has(r.id)} lead=${html`<${Avatar} name=${r.company} />`}
          contact=${mailHref(r.followup_email) ? html`<a href=${mailHref(r.followup_email)}>${r.followup_email}</a>` : html`<span className="dash">No email on file</span>`}
          when=${`emailed ${ago(r.last_sent)}${r.sends > 1 ? `, ${r.sends - 1} follow-up sent` : ''}`}
          onLog=${setLogFor} />`)}</ul>`
        : html`<${Empty} title="No follow-ups due.">Companies you emailed ${FOLLOWUP_DAYS} or more days ago that haven’t replied show up here.</${Empty}>`}
    </section>`}

    <section className="section" aria-labelledby="cn-h">
      <${SectionHead} title=${html`<span id="cn-h">Call next</span>`} count=${s.callNext.length}
        why=${s.scope === 'mine' ? 'your companies, best contact details first' : 'nothing is assigned to you, so these are unassigned companies'} />
      ${s.callNext.length ? html`<ul className="ledger">${s.callNext.slice(0, shown).map((r) => html`<${Entry} key=${r.id} row=${r}
          leaving=${leaving.has(r.id)} lead=${html`<${Grade} g=${r._g} />`}
          contact=${html`<${PhoneLinks} row=${r} max=${1} />`}
          when=${r.location || ''}
          onLog=${setLogFor} />`)}</ul>
        ${s.callNext.length > shown && html`<div className="ledger-more">
          <button type="button" className="btn btn-sm" onClick=${() => setShown((n) => n + PAGE)}>Show ${Math.min(PAGE, s.callNext.length - shown)} more</button></div>`}`
        : html`<${Empty} title="No one to call right now.">Companies show up here when they are in To contact and have a phone number. Run a search or use Find contacts on the Job board to add more.</${Empty}>`}
    </section>

    <section className="section" aria-labelledby="st-h">
      <${SectionHead} title=${html`<span id="st-h">Stale leads</span>`} count=${s.stale.length}
        why=${s.stale.length ? `contacted, added ${STALE_DAYS}+ days ago, no reply` : null} />
      ${s.stale.length ? html`<ul className="ledger">${s.stale.map((r) => html`<${Entry} key=${r.id} row=${r}
          leaving=${leaving.has(r.id)} lead=${html`<${Grade} g=${grade(r)} />`}
          contact=${phones(r)[0] ? html`<${PhoneLinks} row=${r} max=${1} />` : (mailHref(primaryEmail(r)) ? html`<a href=${mailHref(primaryEmail(r))}>${primaryEmail(r)}</a>` : html`<span className="dash">No contact</span>`)}
          when=${html`<${Status} value="contacted" /><div className="flag">added ${daysSince(r.created_at)} days ago</div>`}
          onLog=${setLogFor} />`)}</ul>`
        : html`<${Empty} title="Nothing has gone stale.">A company lands here when it is still in Contacted and was added ${STALE_DAYS} or more days ago.</${Empty}>`}
    </section>`}

    ${logFor && html`<${LogOutcome} job=${logFor} user=${user} onClose=${() => setLogFor(null)} onSaved=${() => removeAfterLog(logFor.id)} />`}
    ${confirmFU && html`<${Confirm} title="Send follow-ups?" busy=${sending}
      body=${`This emails your Follow-up template to ${plural(s.followups.length, 'company', 'companies')}. Each company gets at most 2 follow-ups.`}
      confirmLabel=${`Send ${plural(s.followups.length, 'follow-up')}`} onConfirm=${sendFollowups} onClose=${() => setConfirmFU(false)} />`}
  </div>`;
}
