import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from './Icons';

// Generic spotlight tour engine. Dims the screen with four panels framing a
// cut-out hole around the current step's target (so the highlighted control stays
// clickable), draws a glowing ring, and floats a coach-mark card with a progress
// bar + Back/Next/Skip. Steps are page-aware; when a step lives on another screen
// the engine asks the host to navigate (onNavigate) and waits for the target to
// mount. See data/tourSteps.js for the step shape.
const PAD = 8;          // spotlight padding around the target
const GAP = 14;         // gap between the spotlight and the coach-mark
const MARGIN = 12;      // keep the card this far from the viewport edge
const CARD_W = 340;     // must match .tour-card max-width in app.css

const rectOf = (el) => {
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
};

export default function Tour({ open, steps, page, onNavigate, onClose }) {
  const [idx, setIdx] = useState(0);
  const [rect, setRect] = useState(null);     // target rect, or null (centered)
  const [pos, setPos] = useState(null);       // computed card position
  const cardRef = useRef(null);

  const total = steps.length;
  const step = steps[idx] || null;

  // (Re)start at the first step each time the tour is opened.
  useEffect(() => { if (open) setIdx(0); }, [open]);

  const finish = useCallback(() => { onClose?.(); }, [onClose]);

  // Side effects (navigation/close) live OUTSIDE setIdx — a state updater must be
  // pure, and StrictMode double-invokes it (which would open the workspace twice).
  const go = useCallback((delta) => {
    const ni = idx + delta;
    if (ni < 0) return;
    if (ni >= total) { finish(); return; }
    const ns = steps[ni];
    if (ns && ns.page !== page) onNavigate?.(ns.page);   // cross-screen jump
    setIdx(ni);
  }, [idx, total, steps, page, onNavigate, finish]);

  // If the host screen changes (e.g. the user opened a session themselves), snap
  // the tour to the first step that belongs to the now-visible page.
  useEffect(() => {
    if (!open || !step || step.page === page) return;
    let j = steps.findIndex((s, i) => i >= idx && s.page === page);
    if (j === -1) j = steps.findIndex((s) => s.page === page);
    if (j !== -1 && j !== idx) setIdx(j);
  }, [page, open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Locate the target element, polling briefly so steps survive navigation /
  // async mounts. Centered steps (no target) clear the rect.
  useLayoutEffect(() => {
    if (!open) return undefined;
    if (!step?.target) { setRect(null); return undefined; }
    let raf = 0;
    let tries = 0;
    const find = () => {
      const el = document.querySelector(`[data-tour="${step.target}"]`);
      if (el) {
        try { el.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch { /* older browsers */ }
        setRect(rectOf(el));
      } else if (tries++ < 120) {       // ~2s of polling before giving up
        raf = requestAnimationFrame(find);
      } else {
        setRect(null);                  // fall back to a centered card
      }
    };
    find();
    return () => cancelAnimationFrame(raf);
  }, [open, idx, step?.target, page]);

  // Keep the spotlight aligned while the page scrolls or resizes.
  useEffect(() => {
    if (!open || !step?.target) return undefined;
    const update = () => {
      const el = document.querySelector(`[data-tour="${step.target}"]`);
      if (el) setRect(rectOf(el));
    };
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [open, idx, step?.target]);

  // Position the coach-mark next to the spotlight (or centered), clamped on-screen.
  useLayoutEffect(() => {
    if (!open || !step) return;
    const card = cardRef.current;
    const cw = card?.offsetWidth || CARD_W;
    const ch = card?.offsetHeight || 200;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    if (!rect) { // centered
      setPos({ top: Math.max(MARGIN, (vh - ch) / 2), left: Math.max(MARGIN, (vw - cw) / 2) });
      return;
    }
    const hole = { top: rect.top - PAD, left: rect.left - PAD, w: rect.width + 2 * PAD, h: rect.height + 2 * PAD };
    const cx = hole.left + hole.w / 2;
    const cy = hole.top + hole.h / 2;
    let top;
    let left;
    switch (step.placement) {
      case 'top':    top = hole.top - ch - GAP;     left = cx - cw / 2; break;
      case 'left':   left = hole.left - cw - GAP;   top = cy - ch / 2;  break;
      case 'right':  left = hole.left + hole.w + GAP; top = cy - ch / 2; break;
      case 'center': top = (vh - ch) / 2;           left = (vw - cw) / 2; break;
      default:       top = hole.top + hole.h + GAP; left = cx - cw / 2;  // bottom
    }
    left = Math.max(MARGIN, Math.min(left, vw - cw - MARGIN));
    top = Math.max(MARGIN, Math.min(top, vh - ch - MARGIN));
    setPos({ top, left });
  }, [open, idx, rect, step]);

  // Keyboard: arrows / Enter advance, Esc closes — but defer to any open modal
  // (e.g. the New Session dialog) so its own Enter/Esc keep working.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (document.querySelector('.modal-overlay')) return;
      if (e.key === 'Escape') finish();
      else if (e.key === 'ArrowRight' || e.key === 'Enter') go(1);
      else if (e.key === 'ArrowLeft') go(-1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, go, finish]);

  if (!open || !step) return null;

  const hole = rect
    ? { top: rect.top - PAD, left: rect.left - PAD, w: rect.width + 2 * PAD, h: rect.height + 2 * PAD }
    : null;
  const pct = Math.round(((idx + 1) / total) * 100);
  const isLast = idx === total - 1;
  const stop = (e) => e.stopPropagation();

  return (
    <div className="tour-root" role="dialog" aria-modal="true" aria-label="Product tour">
      {hole ? (
        <>
          {/* Four dim panels frame the hole, leaving the target clickable. */}
          <div className="tour-dim" style={{ top: 0, left: 0, width: '100vw', height: Math.max(0, hole.top) }} />
          <div className="tour-dim" style={{ top: hole.top + hole.h, left: 0, width: '100vw', bottom: 0 }} />
          <div className="tour-dim" style={{ top: hole.top, left: 0, width: Math.max(0, hole.left), height: hole.h }} />
          <div className="tour-dim" style={{ top: hole.top, left: hole.left + hole.w, right: 0, height: hole.h }} />
          <div className="tour-ring" style={{ top: hole.top, left: hole.left, width: hole.w, height: hole.h }} />
        </>
      ) : (
        <div className="tour-dim full" />
      )}

      <div
        className={`tour-card ${hole ? '' : 'centered'}`}
        ref={cardRef}
        onClick={stop}
        style={pos ? { top: pos.top, left: pos.left } : { visibility: 'hidden' }}
      >
        <div className="tour-progress"><span style={{ width: `${pct}%` }} /></div>
        <div className="tour-card-head">
          <span className="tour-count">Step {idx + 1} of {total}</span>
          <button className="icon-btn" onClick={finish} aria-label="Close tour"><Icon.x width={15} height={15} /></button>
        </div>
        <h3 className="tour-title">{step.title}</h3>
        <p className="tour-body">{step.body}</p>
        {step.tip && (
          <div className="tour-tip"><Icon.spark width={13} height={13} /> <span>{step.tip}</span></div>
        )}
        <div className="tour-foot">
          <button className="btn btn-ghost btn-sm" onClick={finish}>Skip</button>
          <span className="spacer" style={{ flex: 1 }} />
          {idx > 0 && <button className="btn btn-ghost btn-sm" onClick={() => go(-1)}><Icon.back width={14} height={14} /> Back</button>}
          <button className="btn btn-primary btn-sm" onClick={() => go(1)}>
            {isLast ? 'Finish' : 'Next'} {!isLast && <Icon.arrowRight width={14} height={14} />}
          </button>
        </div>
      </div>
    </div>
  );
}
