// =============================================================================
// useAgentRunLive — the REAL agent. Streams the Phase-1 engine's trace from the
// backend (server/app.py) over SSE and produces the SAME state shape as the mock
// useAgentRun. Supports BOTH modes:
//   • Autopilot → end-to-end.
//   • Co-pilot  → the engine pauses (AWAITING_USER) before each tool; approve /
//                 edit / reject / guide / cancel are sent back via POST /api/decide.
// Greetings / small talk are answered conversationally by the LLM (/api/chat).
// =============================================================================
import { useCallback, useEffect, useRef, useState } from 'react';
import { EVENT, COMMAND, MODE, RUN_STATE, STEP_KIND } from '../lib/eventContract';
import { runStream, chatLLM, summarizeAnswer, decide } from '../lib/api';
import { classifyIntent, chatReply } from '../lib/chat';
import { humanizeError } from '../lib/errors';

let _id = 0;
const uid = (p) => `${p}_${++_id}`;
const now = () => new Date().toLocaleTimeString('en-GB', { hour12: false });

const HINT_MARKERS = ['🤔', '⚠️', '⚠', '⏸️', '🔄', '🔍'];
function cleanObs(s) {
  s = String(s ?? '');
  let cut = s.length;
  for (const m of HINT_MARKERS) { const i = s.indexOf(m); if (i >= 0) cut = Math.min(cut, i); }
  return s.slice(0, cut).trim();
}
const tryParse = (s) => { try { return JSON.parse(s); } catch { return null; } };
const prettyRaw = (s) => { const o = tryParse(s); return o ? JSON.stringify(o, null, 2) : s; };

const DECOMP_LINE = /^\s*[-*]?\s*(\*\*)?\s*(Q-Restate|Entities|Filters|Aggregation|Output|Not[\s-]?asking)\s*(\*\*)?\s*:/i;
function stripDecomposition(text) {
  const lines = String(text ?? '').split('\n');
  const kept = lines.filter((l) => !DECOMP_LINE.test(l)).join('\n').replace(/\n{3,}/g, '\n\n').trim();
  return kept || String(text ?? '').trim();
}

const PLAN_FIELDS = [['Entities', 'Entities'], ['Filters', 'Filters'], ['Aggregation', 'Aggregation'], ['Output', 'Output'], ['Not-asking', 'Not[\\s-]?asking']];
function parsePlan(text) {
  const fields = [];
  for (const [label, pat] of PLAN_FIELDS) {
    const re = new RegExp(`(?:^|\\n)\\s*[-*]?\\s*\\*{0,2}\\s*${pat}\\s*\\*{0,2}\\s*:\\s*([^\\n]+)`, 'i');
    const m = String(text ?? '').match(re);
    if (m) fields.push({ k: label, v: m[1].replace(/\*/g, '').trim() });
  }
  return fields.length >= 3 ? fields : null;
}

// Detect data-quality / cleaning intent — the Entities/Filters/Aggregation breakdown
// makes no sense for "what's wrong with my data?" questions. We seed a domain plan
// instead so the panel shows something useful from the very first second.
const QUALITY_HINT = /\b(data\s+quality|clean(?:ing|ed|up)?|dirty|missing|nulls?|duplicate|outliers?|format|consist(?:ent|ency)|fix(?:es)?|profile|sanit[iy]ze|messy)\b/i;
const FILE_NAME = /([A-Za-z0-9_\-]+\.(?:csv|tsv|xlsx?|json|sqlite\d?|db|txt|md|pdf))/i;
function detectIntent(q) {
  return QUALITY_HINT.test(String(q || '')) ? 'quality' : 'analytic';
}
function qualityPlan(q) {
  const file = (String(q || '').match(FILE_NAME) || [])[1];
  return {
    intent: 'quality',
    fields: [
      { k: 'Target', v: file || 'the uploaded file' },
      { k: 'Goal', v: 'Profile & diagnose data-quality issues' },
      { k: 'Checks', v: 'Nulls · duplicates · formats · outliers · types' },
      { k: 'Output', v: 'Ranked issues + concrete fix recommendations' },
    ],
  };
}

function summarize(action, rawObs) {
  const clean = cleanObs(rawObs);
  const o = tryParse(clean);
  if (action === 'list_context' && o) {
    const files = (o.entries || []).filter((e) => e.kind === 'file').map((e) => e.path);
    return `Found ${files.length} files` + (files.length ? `: ${files.slice(0, 4).join(', ')}` : '');
  }
  if (action === 'execute_python' && o) {
    if (o.success === false) { const e = String(o.stderr || o.error || '').split('\n').filter(Boolean).pop() || 'error'; return 'Error → ' + e.slice(0, 130); }
    const out = String(o.output || '').trim();
    return out ? 'stdout → ' + out.slice(0, 130) : 'ran (no output)';
  }
  if ((action === 'execute_universal_sql' || action === 'execute_context_sql') && o) {
    const n = o.row_count ?? (Array.isArray(o.rows) ? o.rows.length : null);
    return n != null ? `${n} rows returned` : (o.success === false ? 'query error' : 'query ran');
  }
  if (action && action.startsWith('profile') && o) {
    const r = o.total_rows ?? o.row_count;
    const c = o.total_columns ?? (Array.isArray(o.columns) ? o.columns.length : null);
    return [r != null ? `${r} rows` : null, c != null ? `${c} cols` : null].filter(Boolean).join(' · ') || 'profiled';
  }
  if (action === 'answer' && o) return `Submitted ${o.row_count} rows × ${o.column_count} columns`;
  return clean.slice(0, 150) || 'done';
}

function normalizeAnswer(ans) {
  if (!ans || typeof ans !== 'object') return { columns: [], rows: [], numeric: {} };
  let columns = Array.isArray(ans.columns) ? ans.columns.map(String) : [];
  const rawRows = Array.isArray(ans.rows) ? ans.rows : [];
  // Tolerate two shapes:
  //   1. list-of-lists (what the strict `answer` tool produces)
  //   2. list-of-dicts (records orientation, what some persisted answers and
  //      free-form tool outputs carry). If columns are missing, infer them
  //      from the first dict so we still render a table instead of crashing.
  const isDictRows = rawRows.length > 0 && rawRows.every((r) => r && typeof r === 'object' && !Array.isArray(r));
  if (isDictRows && columns.length === 0) {
    columns = Object.keys(rawRows[0] || {}).map(String);
  }
  const rows = rawRows.map((r) => {
    if (Array.isArray(r)) return r.map((c) => c);
    if (r && typeof r === 'object') return columns.map((c) => r[c]);
    return [r];
  });
  const numeric = {};
  columns.forEach((c, i) => {
    if (rows.length && rows.every((r) => r[i] !== '' && r[i] != null && !isNaN(parseFloat(r[i])))) numeric[c] = true;
  });
  return { columns, rows, numeric };
}

export function useAgentRunLive({ settings, sessionId }) {
  const [mode, setModeState] = useState(MODE.AUTOPILOT);
  const [feed, setFeed] = useState([]);
  const [events, setEvents] = useState([]);
  const [results, setResults] = useState({ stages: [], answer: null, chart: null });
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState(null);

  const activeRunId = useRef(null);     // local feed run id
  const runIdRef = useRef(null);        // backend run id (for /api/decide)
  const abortRef = useRef(null);
  const planSet = useRef(false);
  const pausedRef = useRef(null);       // {actionTraceId} while AWAITING_USER
  const modeRef = useRef(mode);
  const cfg = useRef({ settings, sessionId });
  useEffect(() => { cfg.current = { settings, sessionId }; }, [settings, sessionId]);
  useEffect(() => { modeRef.current = mode; }, [mode]);

  // ---- chat persistence ----------------------------------------------------
  // Save / restore the conversation per session so reloading (or coming back
  // later) doesn't lose history. We keep it intentionally simple: only the
  // user-visible feed + final plan/results are persisted; in-flight runs
  // (busy=true, AWAITING_USER) are demoted to FAILED on load so the UI never
  // shows a stuck "thinking…" spinner from a previous tab.
  const storageKey = sessionId ? `das-chat-${sessionId}` : null;
  const hydratedRef = useRef(null);     // sid we've already restored, so we don't reset feed on rerender
  useEffect(() => {
    if (!storageKey || hydratedRef.current === sessionId) return;
    hydratedRef.current = sessionId;
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) { setFeed([]); setEvents([]); setResults({ stages: [], answer: null, chart: null }); setPlan(null); return; }
      const saved = JSON.parse(raw);
      const cleanFeed = (saved.feed || []).map((e) =>
        e.type === 'run' && e.state !== RUN_STATE.DONE && e.state !== RUN_STATE.FAILED
          ? { ...e, state: RUN_STATE.FAILED, awaiting: null, summary: e.summary || 'Run did not finish (page was closed).' }
          : e);
      setFeed(cleanFeed);
      // Re-normalize persisted answers: older builds may have stored rows as
      // dicts; rendering those directly throws "Objects are not valid as a React child".
      const savedResults = saved.results || { stages: [], answer: null, chart: null };
      if (savedResults.answer) savedResults.answer = normalizeAnswer(savedResults.answer);
      setResults(savedResults);
      setPlan(saved.plan || null);
    } catch { /* corrupt entry — ignore */ }
  }, [storageKey, sessionId]);
  useEffect(() => {
    if (!storageKey || hydratedRef.current !== sessionId) return;
    try { localStorage.setItem(storageKey, JSON.stringify({ feed, results, plan })); }
    catch { /* quota / serialization — silent */ }
  }, [storageKey, sessionId, feed, results, plan]);

  const emit = (type, payload = {}) => setEvents((ev) => [...ev, { id: uid('e'), time: now(), type, payload }]);
  const patchRun = (fn) => setFeed((f) => f.map((e) => (e.type === 'run' && e.id === activeRunId.current ? fn(e) : e)));
  const addTrace = (item) => patchRun((r) => ({ ...r, trace: [...r.trace, { id: uid('t'), ...item }] }));
  const updateTrace = (tid, patch) => patchRun((r) => ({ ...r, trace: r.trace.map((t) => (t.id === tid ? { ...t, ...patch } : t)) }));

  const setMode = useCallback((m) => { setModeState(m); emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.SET_MODE, mode: m }); }, []);

  const send = useCallback(async (text, opts = {}) => {
    if (busy) return;
    const { settings, sessionId } = cfg.current;

    // Chatbot layer: greetings / small talk → LLM reply (no agent run).
    const intent = classifyIntent(text);
    if (intent !== 'question') {
      const mId = uid('m');
      setFeed((f) => [...f, { type: 'user', id: uid('u'), text }, { type: 'msg', id: mId, text: '', pending: true }]);
      try {
        const reply = await chatLLM({ text, session_id: sessionId, model: settings.endpoint.model, api_base: settings.endpoint.apiBase, api_key: settings.endpoint.apiKey, api_version: settings.endpoint.apiVersion || '' });
        setFeed((f) => f.map((e) => (e.id === mId ? { ...e, text: reply || chatReply(intent), pending: false } : e)));
      } catch {
        setFeed((f) => f.map((e) => (e.id === mId ? { ...e, text: chatReply(intent), pending: false } : e)));
      }
      return;
    }
    // Agent runs always operate on the user's uploaded workspace — NEVER a benchmark task.
    // This is what stops the agent from "finding" leftover task fixtures (events.csv, members.csv, …).
    const q = (text || '').trim();
    if (!sessionId) { emit(EVENT.RUN_FINISHED, { succeeded: false, failure_reason: 'Workspace session is not ready yet — try again in a moment.' }); return; }
    if (!q) { emit(EVENT.RUN_FINISHED, { succeeded: false, failure_reason: 'Type a question to analyze your uploaded data.' }); return; }
    const runId = uid('run');
    activeRunId.current = runId;
    setBusy(true);
    setResults({ stages: [], answer: null, chart: null });
    setPlan(null); planSet.current = false; pausedRef.current = null;
    // Data-quality questions get a domain plan immediately — no awkward Q-Restate.
    if (detectIntent(q) === 'quality') {
      setPlan(qualityPlan(q));
      planSet.current = true;
    }
    setFeed((f) => [
      ...f,
      { type: 'user', id: uid('u'), text: q },
      { type: 'run', id: runId, question: q, state: RUN_STATE.THINKING, trace: [], awaiting: null, summary: null },
    ]);
    emit(EVENT.RUN_STARTED, { question: q, sessionId, mode: modeRef.current });

    const body = {
      session_id: sessionId, question: q,
      mode: modeRef.current,
      solution: opts.solution || settings.solution || 'react',
      model: settings.endpoint.model, api_base: settings.endpoint.apiBase, api_key: settings.endpoint.apiKey,
      api_version: settings.endpoint.apiVersion || '', max_steps: settings.endpoint.maxSteps, temperature: settings.endpoint.temperature,
    };

    const ac = new AbortController();
    abortRef.current = ac;
    let lastActionId = null;

    try {
      for await (const ev of runStream(body, ac.signal)) {
        emit(ev.type, ev.payload || {});
        const p = ev.payload || {};
        if (ev.type === EVENT.RUN_STARTED) {
          runIdRef.current = p.run_id || null;
          patchRun((r) => ({ ...r, question: p.question || r.question }));
        } else if (ev.type === EVENT.KNOWLEDGE_GRAPH) {
          // The backend pre-built the Knowledge Graph and injected it into the
          // agent's first message. Surface a compact "primed" chip in the trace
          // so the user can see what map the agent is reasoning on.
          const s = p.summary || {};
          const summaryText =
            `${s.entities ?? 0} entit${(s.entities ?? 0) === 1 ? 'y' : 'ies'} · ` +
            `${s.relationships ?? 0} join${(s.relationships ?? 0) === 1 ? '' : 's'}` +
            (s.constraints ? ` · ${s.constraints} constraint${s.constraints === 1 ? '' : 's'}` : '') +
            (s.metrics ? ` · ${s.metrics} metric${s.metrics === 1 ? '' : 's'}` : '');
          addTrace({ kind: STEP_KIND.KG, summary: summaryText, graph: p });
          patchRun((r) => ({ ...r, knowledgeGraph: p }));
        } else if (ev.type === EVENT.AGENT_THOUGHT) {
          patchRun((r) => ({ ...r, state: RUN_STATE.THINKING }));
          if (!planSet.current) { const f = parsePlan(p.thought); if (f) { setPlan({ fields: f }); planSet.current = true; } }
          addTrace({ kind: STEP_KIND.THOUGHT, text: stripDecomposition(p.thought) });
        } else if (ev.type === EVENT.TOOL_EXECUTION_START) {
          const id = uid('t'); lastActionId = id;
          patchRun((r) => ({ ...r, state: RUN_STATE.TOOL_EXECUTING, trace: [...r.trace, { id, kind: STEP_KIND.ACTION, tool: p.action, input: p.action_input, status: 'running' }] }));
        } else if (ev.type === EVENT.AWAITING_USER) {
          // Co-pilot pause: show the proposed step + the decision card.
          const id = uid('t'); lastActionId = id; pausedRef.current = { actionTraceId: id };
          patchRun((r) => ({
            ...r, state: RUN_STATE.AWAITING_USER,
            trace: [...r.trace, { id, kind: STEP_KIND.ACTION, tool: p.action, input: p.action_input, status: 'proposed' }],
            awaiting: { action: p.action, input: p.action_input, actionTraceId: id, reason: '', sensitive: false },
          }));
        } else if (ev.type === EVENT.TOOL_EXECUTION_SUCCESS || ev.type === EVENT.TOOL_EXECUTION_ERROR) {
          const ok = !!p.ok;
          const aId = lastActionId;
          patchRun((r) => ({ ...r, state: RUN_STATE.OBSERVING, awaiting: null, trace: r.trace.map((t) => (t.id === aId ? { ...t, status: ok ? 'done' : 'error' } : t)) }));
          if (p.action === 'answer') addTrace({ kind: STEP_KIND.ANSWER, summary: summarize('answer', p.observation) });
          else addTrace({ kind: STEP_KIND.OBSERVE, ok, summary: summarize(p.action, p.observation), raw: prettyRaw(cleanObs(p.observation)) });
        } else if (ev.type === EVENT.RUN_FINISHED) {
          const normalized = p.answer ? normalizeAnswer(p.answer) : null;
          if (normalized) setResults((r) => ({ ...r, answer: normalized }));
          // Build a short placeholder reply so the user sees *something* immediately
          // even before the LLM summary arrives — keeps the chat from looking empty.
          const placeholder = p.succeeded
            ? (normalized
                ? `Done — submitted ${normalized.rows.length} row${normalized.rows.length === 1 ? '' : 's'} × ${normalized.columns.length} column${normalized.columns.length === 1 ? '' : 's'}. See the Results panel for the full table.`
                : 'Done.')
            : (p.failure_reason || null);
          patchRun((r) => ({ ...r, state: p.succeeded ? RUN_STATE.DONE : RUN_STATE.FAILED, summary: placeholder, awaiting: null }));
          // Ask the LLM for a friendly natural-language reply (in the user's language)
          // and swap it in when ready. Best-effort: a failure leaves the placeholder.
          if (p.succeeded && normalized && normalized.rows.length > 0) {
            const myRunId = activeRunId.current;
            summarizeAnswer({
              question: q,
              columns: normalized.columns,
              rows: normalized.rows,
              session_id: sessionId,
              model: settings.endpoint.model,
              api_base: settings.endpoint.apiBase,
              api_key: settings.endpoint.apiKey,
              api_version: settings.endpoint.apiVersion || '',
            }).then((reply) => {
              if (!reply) return;
              // Targeted update so a later run started in this session can't be clobbered.
              setFeed((f) => f.map((e) => (e.type === 'run' && e.id === myRunId ? { ...e, summary: reply } : e)));
            }).catch(() => { /* leave placeholder in place */ });
          }
        }
      }
    } catch (err) {
      patchRun((r) => ({ ...r, state: RUN_STATE.FAILED, summary: humanizeError(err).message, awaiting: null }));
      emit(EVENT.RUN_FINISHED, { succeeded: false, failure_reason: String(err.message || err) });
    } finally {
      setBusy(false); abortRef.current = null; runIdRef.current = null; pausedRef.current = null;
    }
  }, [busy]);

  // --- co-pilot decisions → /api/decide -----------------------------------
  const sendDecision = (decision, extra = {}) => { if (runIdRef.current) decide({ run_id: runIdRef.current, decision, ...extra }); };

  const approve = useCallback(() => {
    const paused = pausedRef.current; if (!paused) return;
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.APPROVE });
    updateTrace(paused.actionTraceId, { status: 'running' });
    patchRun((r) => ({ ...r, state: RUN_STATE.TOOL_EXECUTING, awaiting: null }));
    pausedRef.current = null;
    sendDecision('approve');
  }, []);

  const editAndRun = useCallback((newInput) => {
    const paused = pausedRef.current; if (!paused) return;
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.EDIT, details: newInput });
    updateTrace(paused.actionTraceId, { status: 'running', input: newInput });
    patchRun((r) => ({ ...r, state: RUN_STATE.TOOL_EXECUTING, awaiting: null }));
    pausedRef.current = null;
    sendDecision('edit', { action_input: newInput });
  }, []);

  const reject = useCallback(() => {
    const paused = pausedRef.current; if (!paused) return;
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.REJECT });
    updateTrace(paused.actionTraceId, { status: 'rejected' });
    patchRun((r) => ({ ...r, state: RUN_STATE.THINKING, awaiting: null }));
    pausedRef.current = null;
    sendDecision('reject');
  }, []);

  const guide = useCallback((textHint) => {
    const paused = pausedRef.current;
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.GUIDE, text: textHint });
    addTrace({ kind: STEP_KIND.THOUGHT, text: `💡 User guidance: ${textHint}`, guide: true });
    if (paused) {
      updateTrace(paused.actionTraceId, { status: 'rejected' });
      patchRun((r) => ({ ...r, state: RUN_STATE.THINKING, awaiting: null }));
      pausedRef.current = null;
      sendDecision('reject', { note: textHint });
    }
  }, []);

  const cancel = useCallback(() => {
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.CANCEL });
    if (runIdRef.current) sendDecision('cancel');
    abortRef.current?.abort();
    patchRun((r) => ({ ...r, state: RUN_STATE.CANCELLED, awaiting: null }));
    pausedRef.current = null;
    setBusy(false);
  }, []);

  // Wipes the chat for the current session (feed + events + results + plan) and
  // erases the persisted copy in localStorage. If a run is in flight we abort it
  // first so the trace doesn't keep streaming into a cleared feed.
  const clear = useCallback(() => {
    abortRef.current?.abort();
    pausedRef.current = null;
    runIdRef.current = null;
    activeRunId.current = null;
    planSet.current = false;
    setBusy(false);
    setFeed([]);
    setEvents([]);
    setResults({ stages: [], answer: null, chart: null });
    setPlan(null);
    if (storageKey) { try { localStorage.removeItem(storageKey); } catch { /* ignore */ } }
  }, [storageKey]);

  return { mode, setMode, feed, events, results, busy, plan, send, approve, editAndRun, guide, reject, cancel, clear };
}
