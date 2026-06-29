// =============================================================================
// Event & command contract — single source of truth UI⇄core.
// The mock hook emits these locally; a real backend would push the same shapes
// over WebSocket/SSE. Keep these names in sync with the backend.
// =============================================================================

export const MODE = {
  AUTOPILOT: 'autopilot', // End-to-End: agent runs, user watches the trace
  COPILOT: 'copilot',     // Step-by-Step: user approves/edits each step
};

export const RUN_STATE = {
  IDLE: 'IDLE',
  THINKING: 'THINKING',
  STEP_PROPOSED: 'STEP_PROPOSED',
  AWAITING_USER: 'AWAITING_USER',
  TOOL_EXECUTING: 'TOOL_EXECUTING',
  OBSERVING: 'OBSERVING',
  DONE: 'DONE',
  FAILED: 'FAILED',
  CANCELLED: 'CANCELLED',
};

// Event types pushed core → UI.
export const EVENT = {
  SESSION_STARTED: 'SESSION_STARTED',
  RUN_STARTED: 'RUN_STARTED',
  KNOWLEDGE_GRAPH: 'KNOWLEDGE_GRAPH',
  STATE_CHANGED: 'STATE_CHANGED',
  AGENT_THOUGHT: 'AGENT_THOUGHT',
  STEP_PROPOSED: 'STEP_PROPOSED',
  AWAITING_USER: 'AWAITING_USER',
  TOOL_EXECUTION_START: 'TOOL_EXECUTION_START',
  TOOL_EXECUTION_SUCCESS: 'TOOL_EXECUTION_SUCCESS',
  TOOL_EXECUTION_ERROR: 'TOOL_EXECUTION_ERROR',
  USER_COMMAND_APPLIED: 'USER_COMMAND_APPLIED',
  RUN_FINISHED: 'RUN_FINISHED',
};

// Commands pushed UI → core (controller).
export const COMMAND = {
  SET_MODE: 'set_mode',
  APPROVE: 'approve',
  EDIT: 'edit',
  REJECT: 'reject',
  GUIDE: 'guide',
  CANCEL: 'cancel',
};

// Visual grouping of a tool action into a "kind" for the trace timeline.
export const STEP_KIND = { THOUGHT: 'thought', ACTION: 'action', OBSERVE: 'observe', ANSWER: 'answer', KG: 'kg' };
