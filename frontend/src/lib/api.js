// Thin client for the live backend (server/app.py). Change the port here (or set
// localStorage 'das-api') if you run uvicorn on a different port.
export const API_BASE = localStorage.getItem('das-api') || 'http://localhost:8000';

// --- Auth token (kept in localStorage; attached as a Bearer header) ----------
const TOKEN_KEY = 'das-auth-token';
export const getToken = () => localStorage.getItem(TOKEN_KEY) || '';
export const setToken = (t) => { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); };
export const authHeader = () => (getToken() ? { Authorization: `Bearer ${getToken()}` } : {});

export async function registerUser(username, password) {
  const r = await fetch(`${API_BASE}/api/auth/register`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  return jsonOrThrow(r, 'register');
}

export async function loginUser(username, password) {
  const r = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  return jsonOrThrow(r, 'login');
}

// Validate the stored token on app load. Returns { username } or null.
export async function fetchMe() {
  const token = getToken();
  if (!token) return null;
  try {
    const r = await fetch(`${API_BASE}/api/auth/me`, { headers: authHeader() });
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

export async function checkHealth() {
  try {
    const r = await fetch(`${API_BASE}/api/health`, { signal: AbortSignal.timeout(2500) });
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

export async function fetchTasks() {
  const r = await fetch(`${API_BASE}/api/tasks?limit=80`);
  if (!r.ok) throw new Error(`tasks ${r.status}`);
  return r.json();
}

// Conversational small-talk reply from the LLM.
export async function chatLLM(body) {
  const r = await fetch(`${API_BASE}/api/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`chat ${r.status}`);
  const d = await r.json();
  return d.reply || '';
}

// Turn a finished answer-table into a 1-3 sentence conversational reply
// in the user's language. Falls back to '' so the caller can use a template.
export async function summarizeAnswer(body) {
  const r = await fetch(`${API_BASE}/api/summarize-answer`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) return '';
  const d = await r.json();
  return d.reply || '';
}

// Send a co-pilot decision (approve / edit / reject / cancel) for a paused run.
export async function decide(body) {
  const r = await fetch(`${API_BASE}/api/decide`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  return r.ok ? r.json() : { ok: false };
}

// --- Sessions, files & Data Doctor (§12.0 / §12.1) ---------------------------
async function jsonOrThrow(r, label) {
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.detail || `${label} ${r.status}`);
  }
  return r.json();
}

export async function createSession(name) {
  const r = await fetch(`${API_BASE}/api/sessions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  });
  return jsonOrThrow(r, 'create session');
}

export async function listSessions() {
  const r = await fetch(`${API_BASE}/api/sessions`);
  return r.ok ? r.json() : [];
}

export async function renameSession(sid, name) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  });
  return jsonOrThrow(r, 'rename session');
}

export async function getSession(sid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}`);
  return r.ok ? r.json() : null;
}

export async function listFiles(sid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/files`);
  return jsonOrThrow(r, 'list files');
}

export async function uploadFile(sid, file) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/files?filename=${encodeURIComponent(file.name)}`, {
    method: 'POST', body: file,
  });
  return jsonOrThrow(r, 'upload');
}

export async function deleteFile(sid, fid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/files/${fid}`, { method: 'DELETE' });
  return r.ok;
}

export async function listSamples() {
  const r = await fetch(`${API_BASE}/api/samples`);
  return r.ok ? r.json() : [];
}

export async function importSample(sid, name) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/files/from_sample?name=${encodeURIComponent(name)}`, {
    method: 'POST',
  });
  return jsonOrThrow(r, 'import sample');
}

export async function analyzeQuality(sid, filename, creds = {}, opts = {}) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/quality`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, table: opts.table ?? null, ...creds }),
  });
  return jsonOrThrow(r, 'analyze');
}

export async function applyQualityFix(sid, filename, fix, dryRun = false, opts = {}) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/quality/apply`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, fix, dry_run: dryRun, table: opts.table ?? null }),
  });
  return jsonOrThrow(r, 'apply');
}

// §12.5 — ask the backend which solution fits the question + workspace data.
// With model creds the backend asks the LLM; without, it uses a heuristic.
export async function recommendSolution(question, sid, creds = {}) {
  const r = await fetch(`${API_BASE}/api/recommend-solution`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sid ?? null, ...creds }),
  });
  return jsonOrThrow(r, 'recommend');
}

export async function exploreStats(sid, filename, sheet, table) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/explore`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, sheet: sheet ?? null, table: table ?? null }),
  });
  return jsonOrThrow(r, 'explore');
}

export async function buildTextKnowledgeGraph(sid, filename, creds = {}, opts = {}) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/textkg`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, max_pages: opts.maxPages ?? 25, force: opts.force ?? false, ...creds }),
  });
  return jsonOrThrow(r, 'text-kg');
}

// The agent's real tool registry (GET /api/tools) — used to populate the Tools
// panel so it always reflects every tool the engine can call.
export async function listTools() {
  const r = await fetch(`${API_BASE}/api/tools`);
  return jsonOrThrow(r, 'tools');
}

export const downloadUrl = (sid, fid) => `${API_BASE}/api/sessions/${sid}/files/${fid}/download`;

export async function previewFile(sid, fid, rows = 50, opts = {}) {
  const qs = new URLSearchParams({ rows: String(rows) });
  if (opts.table) qs.set('table', opts.table);
  if (opts.sheet) qs.set('sheet', opts.sheet);
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/files/${fid}/preview?${qs}`);
  return jsonOrThrow(r, 'preview');
}

// --- Multi-DB ER schema (§12.2b RelationshipGraph live data) ---------------
export async function getSessionSchema(sid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/schema`);
  return jsonOrThrow(r, 'schema');
}

export async function getFileSchema(sid, fid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/files/${fid}/schema`);
  return jsonOrThrow(r, 'file schema');
}

// --- Analysis history (saved Data Doctor results) ---------------------------
export async function listAnalyses(sid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/analyses`);
  return r.ok ? r.json() : [];
}

export async function getAnalysis(sid, aid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/analyses/${aid}`);
  return jsonOrThrow(r, 'load analysis');
}

export async function deleteAnalysis(sid, aid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/analyses/${aid}`, { method: 'DELETE' });
  return r.ok;
}

export async function clearAnalyses(sid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}/analyses`, { method: 'DELETE' });
  return r.ok ? r.json() : { ok: false };
}

export async function deleteSession(sid) {
  const r = await fetch(`${API_BASE}/api/sessions/${sid}`, { method: 'DELETE' });
  return r.ok;
}

// POST /api/run and yield parsed SSE event objects as they arrive.
export async function* runStream(body, signal) {
  const r = await fetch(`${API_BASE}/api/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`run failed: HTTP ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const frames = buf.split('\n\n');
    buf = frames.pop() || '';
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      try { yield JSON.parse(line.slice(5).trim()); } catch { /* ignore */ }
    }
  }
}
