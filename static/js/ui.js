// Shared components.
import {
  html, useState, useEffect, useRef, api, toast, toastEvents, STATUSES, statusLabel,
  OUTCOME_LABEL, CHANNEL_LABEL, GRADE_HELP, initials, dateTime, phones, emails, primaryEmail,
  telHref, mailHref, bumpLoggedToday,
} from './lib.js';

// ── Icons (24px grid, 1.75 stroke) ──────────────────────────────────────
const P = {
  today: 'M8 2v4M16 2v4M3 9h18M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM8.5 15l2.2 2.2 4.8-4.7',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.5-3.5',
  board: 'M3 5h18M3 12h18M3 19h18M8 5v14',
  pipeline: 'M4 4h4v16H4zM10 4h4v11h-4zM16 4h4v7h-4z',
  mail: 'M3 6h18v12H3zM3 7l9 6 9-6',
  book: 'M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2V4zM5 18a2 2 0 0 1 2-2h12M9 8h6',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.6 3.7 5.6 3.7 9s-1.2 6.4-3.7 9c-2.5-2.6-3.7-5.6-3.7-9S9.5 5.6 12 3z',
  settings: 'M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M10 15v4',
  chart: 'M4 20V10M10 20V4M16 20v-7M2 20h20',
  users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 6 6v1M16 3.5a4 4 0 0 1 0 7.5M22 21v-1a6 6 0 0 0-4-5.6',
  menu: 'M4 7h16M4 12h16M4 17h16',
  x: 'M6 6l12 12M18 6 6 18',
  phone: 'M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  sort: 'M8 9l4-4 4 4M8 15l4 4 4-4',
  up: 'M8 14l4-4 4 4',
  down: 'M8 10l4 4 4-4',
  signout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
  refresh: 'M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4',
  download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
};
export function Icon({ name, size = 18, label }) {
  return html`<svg width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"
    aria-hidden=${label ? null : 'true'} role=${label ? 'img' : null} aria-label=${label || null}>
    <path d=${P[name]} /></svg>`;
}

export function Grade({ g }) {
  return html`<span className=${'grade grade-' + g} title=${'Grade ' + g + ': ' + GRADE_HELP[g]}
    aria-label=${'Grade ' + g + ', ' + GRADE_HELP[g]}>${g}</span>`;
}
export function Status({ value }) {
  const v = value || 'new';
  return html`<span className=${'status st-' + v}><span className="swatch" aria-hidden="true"></span>${statusLabel(v)}</span>`;
}
export function StatusSelect({ value, onChange, label = 'Status' }) {
  const v = value || 'new';
  return html`<span className=${'status-select st-' + v} onClick=${(e) => e.stopPropagation()}>
    <span className="swatch" aria-hidden="true"></span>
    <select className="select" aria-label=${label} value=${v} onChange=${(e) => onChange(e.target.value)}>
      ${STATUSES.map(([k, l]) => html`<option key=${k} value=${k}>${l}</option>`)}
    </select></span>`;
}
export const Avatar = ({ name, large, small, title }) =>
  html`<span className=${'avatar' + (large ? ' avatar-lg' : '') + (small ? ' avatar-sm' : '')} aria-hidden="true" title=${title || null}>${name ? initials(name) : ''}</span>`;

// Activity history for one company, newest first.
export function ActivityList({ jobId, refresh = 0, empty = 'Nothing logged yet. Use Log outcome after you call or email.' }) {
  const [acts, setActs] = useState(null);
  useEffect(() => {
    setActs(null);
    api.get(`/api/jobs/${encodeURIComponent(jobId)}/activities`).then((d) => setActs(d.activities || [])).catch(() => setActs([]));
  }, [jobId, refresh]);
  if (acts === null) return html`<p className="muted">Loading history…</p>`;
  if (!acts.length) return html`<p className="muted">${empty}</p>`;
  return html`<ul className="timeline">${acts.map((a, i) => html`<li key=${a.id || i}>
    <div className="t-top"><b>${a.user_name || 'Someone'}</b>
      <span>${(a.channels || [a.channel]).filter(Boolean).map((c) => CHANNEL_LABEL[c] || c).join(' and ')}</span>
      ${a.outcome && html`<span>${OUTCOME_LABEL[a.outcome] || a.outcome}</span>`}
      <time>${dateTime(a.created_at)}</time></div>${a.note && html`<p>${a.note}</p>`}</li>`)}</ul>`;
}

// ── Layers: Esc closes the top-most one, focus moves in and comes back ──
const layers = [];
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && layers.length) { e.preventDefault(); layers[layers.length - 1].current(); }
});
function useLayer(onClose, ref) {
  const cb = useRef(onClose); cb.current = onClose;
  useEffect(() => {
    layers.push(cb);
    const back = document.activeElement;
    const el = ref.current;
    const first = el && (el.querySelector('[data-autofocus]') || el.querySelector('button, [href], input, select, textarea'));
    if (first) first.focus();
    const trap = (e) => {
      if (e.key !== 'Tab' || layers[layers.length - 1] !== cb || !el) return;
      const f = [...el.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])')]
        .filter((n) => n.offsetParent !== null);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    };
    document.addEventListener('keydown', trap);
    document.body.style.overflow = 'hidden';
    return () => {
      const i = layers.indexOf(cb); if (i >= 0) layers.splice(i, 1);
      document.removeEventListener('keydown', trap);
      if (!layers.length) document.body.style.overflow = '';
      if (back && back.focus && document.contains(back)) back.focus();
    };
  }, []);
}

export function Dialog({ title, sub, onClose, children, footer, labelId = 'dlg-title' }) {
  const ref = useRef(null);
  useLayer(onClose, ref);
  return html`<div>
    <div className="scrim" onClick=${onClose}></div>
    <div className="dialog" role="dialog" aria-modal="true" aria-labelledby=${labelId} ref=${ref}>
      <div className="dialog-head">
        <div><h2 id=${labelId}>${title}</h2>${sub && html`<p>${sub}</p>`}</div>
        <button type="button" className="btn btn-quiet btn-icon" onClick=${onClose} aria-label="Close"><${Icon} name="x" /></button>
      </div>
      <div className="dialog-body">${children}</div>
      ${footer && html`<div className="dialog-foot">${footer}</div>`}
    </div></div>`;
}

export function Panel({ title, onClose, children, lead }) {
  const ref = useRef(null);
  useLayer(onClose, ref);
  return html`<div>
    <div className="scrim" onClick=${onClose}></div>
    <aside className="panel" role="dialog" aria-modal="true" aria-labelledby="panel-title" ref=${ref}>
      <div className="panel-head">${lead}<h2 id="panel-title">${title}</h2>
        <button type="button" className="btn btn-quiet btn-icon" onClick=${onClose} aria-label="Close"><${Icon} name="x" /></button>
      </div>
      <div className="panel-body">${children}</div>
    </aside></div>`;
}

export function Confirm({ title, body, confirmLabel, onConfirm, onClose, busy }) {
  return html`<${Dialog} title=${title} onClose=${onClose} labelId="confirm-title"
    footer=${html`<button type="button" className="btn" onClick=${onClose}>Cancel</button>
      <button type="button" className="btn btn-primary" onClick=${onConfirm} disabled=${busy} data-autofocus>${busy ? 'Working…' : confirmLabel}</button>`}>
    <p>${body}</p></${Dialog}>`;
}

export function Toasts() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const on = (e) => {
      const t = e.detail;
      setItems((xs) => [...xs.slice(-2), t]);
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== t.id)), t.tone === 'error' ? 7000 : 4000);
    };
    toastEvents.addEventListener('toast', on);
    return () => toastEvents.removeEventListener('toast', on);
  }, []);
  return html`<div className="toasts" role="status" aria-live="polite">
    ${items.map((t) => html`<div key=${t.id} className=${'toast' + (t.tone === 'error' ? ' is-error' : '')}>${t.text}</div>`)}
  </div>`;
}

// Contact links used in Today and the job detail panel.
export function PhoneLinks({ row, max = 2 }) {
  const ps = phones(row).slice(0, max);
  if (!ps.length) return html`<span className="dash">No phone</span>`;
  return ps.map((p) => {
    const h = telHref(p);
    return h ? html`<a key=${p} className="tel" href=${h}>${p}</a>` : html`<span key=${p}>${p}</span>`;
  });
}

// ── Log outcome ─────────────────────────────────────────────────────────
// The four one-tap outcomes are values POST /api/activities already accepts.
const ONE_TAP = ['interested', 'no_answer', 'callback', 'not_interested'];
const MORE = ['reply_received', 'drive_scheduled'];

export function LogOutcome({ job, user, onClose, onSaved }) {
  const [channels, setChannels] = useState(new Set([phones(job).length ? 'call' : 'email']));
  const [outcome, setOutcome] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [history, setHistory] = useState(null);

  useEffect(() => {
    if (!job.id) { setHistory([]); return; }
    api.get(`/api/jobs/${encodeURIComponent(job.id)}/activities`)
      .then((d) => setHistory(d.activities || [])).catch(() => setHistory([]));
  }, [job.id]);

  const toggle = (c) => setChannels((s) => { const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n; });

  async function save() {
    if (!channels.size) { setErr('Pick how you reached them.'); return; }
    if (!outcome && !note.trim()) { setErr('Pick what happened, or write a note.'); return; }
    setBusy(true); setErr('');
    try {
      await api.post('/api/activities', {
        job_id: job.id, company: job.company, channels: [...channels], outcome, note: note.trim(),
      });
      bumpLoggedToday(user && user.email);
      toast(`Logged for ${job.company || 'this company'}: ${OUTCOME_LABEL[outcome] || 'note'}`);
      onSaved && onSaved(outcome);
      onClose();
    } catch (e) { setErr(e.message); setBusy(false); }
  }

  const contact = phones(job)[0] || primaryEmail(job);
  return html`<${Dialog} title=${'Log outcome'} sub=${[job.company, contact].filter(Boolean).join(', ')} onClose=${onClose} labelId="log-title"
    footer=${html`<button type="button" className="btn" onClick=${onClose}>Cancel</button>
      <button type="button" className="btn btn-primary" onClick=${save} disabled=${busy}>${busy ? 'Saving…' : 'Log outcome'}</button>`}>
    <fieldset style=${{ border: 0, padding: 0, margin: 0 }}>
      <legend className="label" style=${{ marginBottom: 8 }}>How did you reach them?</legend>
      <div className="choice-row">
        ${['call', 'email', 'whatsapp'].map((c) => html`<label key=${c} className="choice">
          <input type="checkbox" checked=${channels.has(c)} onChange=${() => toggle(c)} />${CHANNEL_LABEL[c]}</label>`)}
      </div>
    </fieldset>
    <fieldset style=${{ border: 0, padding: 0, margin: 0 }}>
      <legend className="label" style=${{ marginBottom: 8 }}>What happened?</legend>
      <div className="outcomes">
        ${ONE_TAP.map((o, i) => html`<label key=${o} className="choice">
          <input type="radio" name="outcome" value=${o} checked=${outcome === o} onChange=${() => setOutcome(o)} data-autofocus=${i === 0 ? '' : null} />${OUTCOME_LABEL[o]}</label>`)}
        ${MORE.map((o) => html`<label key=${o} className="choice">
          <input type="radio" name="outcome" value=${o} checked=${outcome === o} onChange=${() => setOutcome(o)} />${OUTCOME_LABEL[o]}</label>`)}
      </div>
    </fieldset>
    <div className="field">
      <label for="log-note">Note <span className="hint">optional</span></label>
      <textarea id="log-note" className="textarea" value=${note} onInput=${(e) => setNote(e.target.value)}
        placeholder="For example: spoke to Priya in HR, call again after 4 Nov"></textarea>
    </div>
    ${err && html`<p className="formerr" role="alert">${err}</p>`}
    <div>
      <h3 className="label" style=${{ marginBottom: 4 }}>Earlier</h3>
      ${history === null ? html`<p className="muted">Loading history…</p>`
        : !history.length ? html`<p className="muted">Nothing logged for this company yet.</p>`
        : html`<ul className="timeline">${history.slice(0, 6).map((a, i) => html`<li key=${a._id || a.id || i}>
            <div className="t-top"><b>${a.user_name || 'Someone'}</b>
              <span>${(a.channels || [a.channel]).filter(Boolean).map((c) => CHANNEL_LABEL[c] || c).join(' and ')}</span>
              ${a.outcome && html`<span>${OUTCOME_LABEL[a.outcome] || a.outcome}</span>`}
              <time>${dateTime(a.created_at || a.timestamp)}</time></div>
            ${a.note && html`<p>${a.note}</p>`}</li>`)}</ul>`}
    </div>
  </${Dialog}>`;
}

export function Empty({ title, children }) {
  return html`<div className="empty"><b>${title}</b>${children}</div>`;
}
export function ErrorLine({ message, onRetry }) {
  return html`<p className="errorline" role="alert">${message}${onRetry && html`<button type="button" className="btn btn-sm" onClick=${onRetry}>Try again</button>`}</p>`;
}

// ── Menu button (used for "Move to…"). Arrow keys move, Enter picks, Esc closes. ──
export function MenuButton({ label, items, onPick, className = 'btn btn-sm', children, ariaLabel }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const btn = useRef(null), menu = useRef(null);
  function show(e) {
    e.stopPropagation();
    const r = btn.current.getBoundingClientRect();
    const h = items.length * 40 + 12;
    const top = r.bottom + 4 + h > window.innerHeight ? Math.max(8, r.top - 4 - h) : r.bottom + 4;
    setPos({ top, left: Math.min(r.left, window.innerWidth - 216) });
    setOpen(true);
  }
  useEffect(() => {
    if (!open) return;
    const first = menu.current && menu.current.querySelector('button:not([disabled])');
    first && first.focus();
    const away = (e) => { if (menu.current && !menu.current.contains(e.target) && e.target !== btn.current) setOpen(false); };
    const close = () => setOpen(false);
    document.addEventListener('mousedown', away);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => { document.removeEventListener('mousedown', away); window.removeEventListener('scroll', close, true); window.removeEventListener('resize', close); };
  }, [open]);
  function onKey(e) {
    const bs = [...menu.current.querySelectorAll('button:not([disabled])')];
    const i = bs.indexOf(document.activeElement);
    if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); setOpen(false); btn.current.focus(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); bs[(i + 1) % bs.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
    else if (e.key === 'Home') { e.preventDefault(); bs[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); bs[bs.length - 1].focus(); }
  }
  return html`<span onClick=${(e) => e.stopPropagation()}>
    <button ref=${btn} type="button" className=${className} aria-haspopup="menu" aria-expanded=${open}
      aria-label=${ariaLabel || null} onClick=${(e) => (open ? setOpen(false) : show(e))}>${children || label}</button>
    ${open && pos && html`<div ref=${menu} className="menu" role="menu" style=${{ position: 'fixed', top: pos.top, left: pos.left }} onKeyDown=${onKey}>
      ${items.map((it) => html`<button key=${it.value} type="button" role="menuitem" disabled=${it.disabled}
        onClick=${() => { setOpen(false); btn.current.focus(); onPick(it.value); }}>${it.label}${it.note && html`<span className="muted small" style=${{ marginLeft: 'auto' }}>${it.note}</span>`}</button>`)}
    </div>`}
  </span>`;
}
