// =============================================================================
// useAgentRun — drives a single chat session's agent runs.
//
// In this scaffold it REPLAYS a scripted run (mockData) with timers so the trace
// streams like a live agent. The two interaction modes are honoured exactly as designed:
//   • Autopilot → steps auto-execute; it only pauses on a `requiresApproval` tool.
//   • Co-pilot  → it pauses (AWAITING_USER) before EVERY tool, awaiting a command.
//
// To go live: keep this hook's shape, but replace the timer engine with a
// WebSocket that consumes EVENT.* messages and whose approve()/edit()/… send
// COMMAND.* messages back. The components never need to change.
// =============================================================================
import { useCallback, useEffect, useRef, useState } from 'react';
import { EVENT, COMMAND, MODE, RUN_STATE, STEP_KIND } from '../lib/eventContract';
import { SCRIPTED_RUN, ANSWER_TABLE, CHART } from '../data/mockData';
import { classifyIntent, chatReply } from '../lib/chat';

let _id = 0;
const uid = (p) => `${p}_${++_id}`;
const now = () => new Date().toLocaleTimeString('en-GB', { hour12: false });

export function useAgentRun() {
  const [mode, setModeState] = useState(MODE.AUTOPILOT);
  const [feed, setFeed] = useState([]); // ordered chat entries: {type:'user'|'run', ...}
  const [events, setEvents] = useState([]); // full AgentEvent log (for Results → Event Log)
  const [results, setResults] = useState({ stages: [], answer: null, chart: null });
  const [busy, setBusy] = useState(false);

  const modeRef = useRef(mode);
  const timers = useRef([]);
  const resumeRef = useRef(null); // continuation while AWAITING_USER
  const activeRunId = useRef(null);

  useEffect(() => { modeRef.current = mode; }, [mode]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const wait = (ms, fn) => { const t = setTimeout(fn, ms); timers.current.push(t); };

  const emit = useCallback((type, payload = {}) => {
    setEvents((ev) => [...ev, { id: uid('e'), time: now(), type, payload }]);
  }, []);

  const patchRun = useCallback((fn) => {
    setFeed((f) => f.map((e) => (e.type === 'run' && e.id === activeRunId.current ? fn(e) : e)));
  }, []);

  const setRunState = useCallback((state) => {
    patchRun((r) => ({ ...r, state }));
    emit(EVENT.STATE_CHANGED, { to: state });
  }, [patchRun, emit]);

  const addTrace = useCallback((item) => {
    patchRun((r) => ({ ...r, trace: [...r.trace, { id: uid('t'), ...item }] }));
  }, [patchRun]);

  const updateTrace = useCallback((traceId, patch) => {
    patchRun((r) => ({ ...r, trace: r.trace.map((t) => (t.id === traceId ? { ...t, ...patch } : t)) }));
  }, [patchRun]);

  const setMode = useCallback((m) => {
    setModeState(m);
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.SET_MODE, mode: m });
  }, [emit]);

  // --- run engine ----------------------------------------------------------
  const steps = SCRIPTED_RUN.steps;

  const finishRun = useCallback(() => {
    setResults((r) => ({ ...r, answer: ANSWER_TABLE }));
    patchRun((r) => ({ ...r, state: RUN_STATE.DONE, summary: SCRIPTED_RUN.finalSummary, awaiting: null }));
    emit(EVENT.RUN_FINISHED, { succeeded: true });
    setBusy(false);
    resumeRef.current = null;
  }, [patchRun, emit]);

  const executeStep = useCallback((i, actionTraceId, overrideInput) => {
    const step = steps[i];
    patchRun((r) => ({ ...r, awaiting: null }));
    updateTrace(actionTraceId, { status: 'running', input: overrideInput || step.actionInput });
    setRunState(RUN_STATE.TOOL_EXECUTING);
    emit(EVENT.TOOL_EXECUTION_START, { action: step.action, action_input: overrideInput || step.actionInput });

    wait(step.durationMs, () => {
      updateTrace(actionTraceId, { status: 'done' });
      emit(step.ok ? EVENT.TOOL_EXECUTION_SUCCESS : EVENT.TOOL_EXECUTION_ERROR, {
        action: step.action, ok: step.ok, observation: step.observation,
      });

      if (step.kind === STEP_KIND.ANSWER) {
        addTrace({ kind: STEP_KIND.ANSWER, summary: step.observation.summary });
        wait(350, finishRun);
        return;
      }

      addTrace({ kind: STEP_KIND.OBSERVE, ok: step.ok, summary: step.observation.summary, details: step.details });
      if (step.produces) setResults((r) => ({ ...r, stages: addStage(r.stages, step.produces) }));
      if (step.producesChart) setResults((r) => ({ ...r, chart: CHART }));
      setRunState(RUN_STATE.OBSERVING);
      wait(300, () => runStep(i + 1));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchRun, updateTrace, setRunState, emit, addTrace, finishRun]);

  const runStep = useCallback((i) => {
    if (i >= steps.length) { finishRun(); return; }
    const step = steps[i];

    setRunState(RUN_STATE.THINKING);
    emit(EVENT.AGENT_THOUGHT, { thought: step.thought });
    addTrace({ kind: STEP_KIND.THOUGHT, text: step.thought });

    wait(650, () => {
      const actionTraceId = uid('t');
      patchRun((r) => ({
        ...r,
        trace: [...r.trace, { id: actionTraceId, kind: STEP_KIND.ACTION, tool: step.action, input: step.actionInput, reason: step.reason, status: 'proposed' }],
      }));
      emit(EVENT.STEP_PROPOSED, { action: step.action, action_input: step.actionInput, reason: step.reason });

      const mustPause = modeRef.current === MODE.COPILOT || step.requiresApproval;
      if (mustPause) {
        setRunState(RUN_STATE.AWAITING_USER);
        emit(EVENT.AWAITING_USER, { action: step.action, action_input: step.actionInput });
        patchRun((r) => ({
          ...r,
          awaiting: {
            stepIndex: i, actionTraceId, action: step.action, input: step.actionInput,
            reason: step.reason, sensitive: !!step.requiresApproval,
          },
        }));
        resumeRef.current = (override) => executeStep(i, actionTraceId, override);
      } else {
        executeStep(i, actionTraceId);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setRunState, emit, addTrace, patchRun, executeStep, finishRun]);

  // --- public controls -----------------------------------------------------
  const send = useCallback((text) => {
    if (busy) return;
    // Chatbot layer: greetings / small talk get a reply, not an agent run.
    const intent = classifyIntent(text);
    if (intent !== 'question') {
      setFeed((f) => [...f, { type: 'user', id: uid('u'), text }, { type: 'msg', id: uid('m'), text: chatReply(intent) }]);
      return;
    }
    const typed = (text || '').trim();
    // Demo mode only plays its scripted example for the *sample* question (or an empty
    // „run sample‟ click). For any other user question we send a clear hint instead
    // of silently replaying an unrelated task — the real answer lives in Live mode.
    if (typed && typed.toLowerCase() !== SCRIPTED_RUN.question.toLowerCase()) {
      setFeed((f) => [
        ...f,
        { type: 'user', id: uid('u'), text: typed },
        { type: 'msg', id: uid('m'), text:
          "You're in **Demo** mode, which only replays a fixed scripted example and can't actually analyze your question. " +
          "Toggle **Live** at the top of this panel (with your model configured in Settings) to run the real agent on your uploaded data." },
      ]);
      return;
    }
    const q = typed || SCRIPTED_RUN.question;
    const runId = uid('run');
    activeRunId.current = runId;
    setBusy(true);
    setFeed((f) => [
      ...f,
      { type: 'user', id: uid('u'), text: q },
      { type: 'run', id: runId, question: q, state: RUN_STATE.THINKING, trace: [], awaiting: null, summary: null },
    ]);
    emit(EVENT.RUN_STARTED, { question: q, mode: modeRef.current });
    wait(250, () => runStep(0));
  }, [busy, emit, runStep]);

  const approve = useCallback(() => {
    if (!resumeRef.current) return;
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.APPROVE });
    const fn = resumeRef.current; resumeRef.current = null; fn();
  }, [emit]);

  const editAndRun = useCallback((newInput) => {
    if (!resumeRef.current) return;
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.EDIT, details: newInput });
    const fn = resumeRef.current; resumeRef.current = null; fn(newInput);
  }, [emit]);

  const guide = useCallback((textHint) => {
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.GUIDE, text: textHint });
    patchRun((r) => ({ ...r, trace: [...r.trace, { id: uid('t'), kind: STEP_KIND.THOUGHT, text: `💡 User guidance: ${textHint}`, guide: true }] }));
  }, [emit, patchRun]);

  const reject = useCallback(() => {
    const fn = resumeRef.current;
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.REJECT });
    resumeRef.current = null;
    patchRun((r) => {
      const awaiting = r.awaiting;
      const trace = r.trace.map((t) => (awaiting && t.id === awaiting.actionTraceId ? { ...t, status: 'rejected' } : t));
      return { ...r, awaiting: null, trace: [...trace, { id: uid('t'), kind: STEP_KIND.OBSERVE, ok: false, summary: 'Step rejected by user — agent will reconsider.', rejected: true }] };
    });
    // for the mock, skip to the next step's reasoning
    const idx = feedAwaitingIndex();
    if (idx != null) wait(400, () => runStep(idx + 1));
    if (fn) { /* discard continuation */ }
  }, [emit, patchRun, runStep]);

  const cancel = useCallback(() => {
    timers.current.forEach(clearTimeout); timers.current = [];
    resumeRef.current = null;
    emit(EVENT.USER_COMMAND_APPLIED, { command: COMMAND.CANCEL });
    patchRun((r) => ({ ...r, state: RUN_STATE.CANCELLED, awaiting: null }));
    setBusy(false);
  }, [emit, patchRun]);

  // helper: read the awaiting step index from current feed
  const feedRef = useRef(feed);
  useEffect(() => { feedRef.current = feed; }, [feed]);
  function feedAwaitingIndex() {
    const run = feedRef.current.find((e) => e.type === 'run' && e.id === activeRunId.current);
    return run?.awaiting?.stepIndex ?? null;
  }

  // Wipes the chat — used by the `/clear` command and the clear button.
  // Cancels any timers from a still-playing scripted run so the empty feed stays empty.
  const clear = useCallback(() => {
    timers.current.forEach(clearTimeout); timers.current = [];
    resumeRef.current = null;
    activeRunId.current = null;
    setBusy(false);
    setFeed([]);
    setEvents([]);
    setResults({ stages: [], answer: null, chart: null });
  }, []);

  return { mode, setMode, feed, events, results, busy, send, approve, editAndRun, guide, reject, cancel, clear };
}

function addStage(stages, produces) {
  const exists = stages.find((s) => s.stage === produces.stage && s.artifact === produces.artifact);
  if (exists) return stages;
  return [...stages, { stage: produces.stage, artifact: produces.artifact, type: produces.type }];
}
