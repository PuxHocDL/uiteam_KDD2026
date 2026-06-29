// =============================================================================
// Tool catalog — the agent's capabilities (plug-and-play).
// `category` drives the activity animation; `icon` is a key into the icon map.
// The live list comes from GET /api/tools (mapServerTools below) so the UI never
// drifts from the real registry; BUILTIN_TOOLS is the offline fallback and must
// mirror src/data_agent_baseline/tools/registry.py. End users can register more
// via the Tools modal — equivalent to dropping a tool YAML.
// =============================================================================

// name → { icon, category }. category feeds the activity animation; keep it to
// the known set: list | scan | sql | python | code | answer.
const TOOL_META = {
  list_context: { icon: 'list', category: 'list' },
  profile_context: { icon: 'list', category: 'scan' },
  read_doc: { icon: 'file', category: 'scan' },
  read_doc_chunk: { icon: 'file', category: 'scan' },
  search_doc: { icon: 'search', category: 'scan' },
  read_pdf: { icon: 'file', category: 'scan' },
  read_csv: { icon: 'table', category: 'scan' },
  profile_csv: { icon: 'table', category: 'scan' },
  profile_quality: { icon: 'table', category: 'scan' },
  read_json: { icon: 'data', category: 'scan' },
  profile_json: { icon: 'data', category: 'scan' },
  profile_database: { icon: 'data', category: 'scan' },
  inspect_sqlite_schema: { icon: 'data', category: 'scan' },
  extract_info: { icon: 'search', category: 'scan' },
  build_knowledge_graph: { icon: 'spark', category: 'scan' },
  read_knowledge_graph: { icon: 'spark', category: 'scan' },
  map_sources: { icon: 'globe', category: 'scan' },
  classify_question: { icon: 'tool', category: 'scan' },
  plan_task: { icon: 'tool', category: 'scan' },
  execute_context_sql: { icon: 'data', category: 'sql' },
  execute_universal_sql: { icon: 'data', category: 'sql' },
  execute_python: { icon: 'code', category: 'python' },
  answer: { icon: 'check', category: 'answer' },
};

export const metaForTool = (name) => TOOL_META[name] || { icon: 'tool', category: 'scan' };

// Group tools by what they work on, so a long registry reads as labelled sections
// (Explore / CSV & JSON / Databases / Documents / …) instead of 23 flat rows.
export const TOOL_GROUPS = [
  { id: 'workspace', label: 'Explore' },
  { id: 'tables', label: 'CSV & JSON' },
  { id: 'db', label: 'Databases' },
  { id: 'docs', label: 'Documents · PDF / text' },
  { id: 'graph', label: 'Knowledge graph' },
  { id: 'compute', label: 'Run & answer' },
  { id: 'other', label: 'Other' },
];

const TOOL_GROUP = {
  list_context: 'workspace', profile_context: 'workspace', map_sources: 'workspace',
  classify_question: 'workspace', plan_task: 'workspace', extract_info: 'workspace',
  read_csv: 'tables', profile_csv: 'tables', profile_quality: 'tables',
  read_json: 'tables', profile_json: 'tables',
  inspect_sqlite_schema: 'db', profile_database: 'db',
  read_doc: 'docs', read_doc_chunk: 'docs', search_doc: 'docs', read_pdf: 'docs',
  build_knowledge_graph: 'graph', read_knowledge_graph: 'graph',
  execute_context_sql: 'compute', execute_universal_sql: 'compute',
  execute_python: 'compute', answer: 'compute',
};

export const groupForTool = (name) => TOOL_GROUP[name] || 'other';

// Bucket tools into TOOL_GROUPS order (custom / unknown → "Other"); drop empties.
export function groupTools(tools) {
  const byId = Object.fromEntries(TOOL_GROUPS.map((g) => [g.id, []]));
  (tools || []).forEach((t) => { (byId[groupForTool(t.name)] || byId.other).push(t); });
  return TOOL_GROUPS.map((g) => ({ ...g, tools: byId[g.id] })).filter((g) => g.tools.length);
}

// Offline fallback — the full registry as of writing. Kept in registry order.
export const BUILTIN_TOOLS = [
  { name: 'list_context', desc: 'List every file in the task context folder.' },
  { name: 'profile_context', desc: 'Profile the whole context (files, DBs, docs) in one bounded call.' },
  { name: 'build_knowledge_graph', desc: 'Map tables, joins, and constraints in one call; saved to a DB for reuse.' },
  { name: 'read_knowledge_graph', desc: 'Read the saved graph; with a query, locate which file/table/value holds a term.' },
  { name: 'map_sources', desc: 'Relate every file ACROSS types (csv/json/db + pdf/md/txt); links docs to tables.' },
  { name: 'classify_question', desc: 'Recommend which architecture fits (react/dragin/multi/hybrid_b).' },
  { name: 'plan_task', desc: 'Locate where each entity lives, then lay out a grounded step plan (no execution).' },
  { name: 'profile_database', desc: 'Full SQLite overview: schemas, row counts, stats, samples, foreign keys.' },
  { name: 'inspect_sqlite_schema', desc: 'Inspect tables and columns in a sqlite/db file.' },
  { name: 'profile_csv', desc: 'Profile a CSV: columns, types, stats, top values.' },
  { name: 'profile_json', desc: 'Profile a JSON file structure without loading raw values.' },
  { name: 'profile_quality', desc: 'Factual per-column quality profile: nulls, types, duplicates.' },
  { name: 'read_csv', desc: 'Tiny CSV preview (5 rows).' },
  { name: 'read_json', desc: 'Tiny JSON preview.' },
  { name: 'read_doc', desc: 'Read the beginning of a text document such as knowledge.md.' },
  { name: 'read_doc_chunk', desc: 'Page through a long document by character offset.' },
  { name: 'search_doc', desc: 'RAG-style keyword/regex search over a long .md/.txt document.' },
  { name: 'read_pdf', desc: 'Extract PDF (or md/txt) text page by page via pypdf.' },
  { name: 'extract_info', desc: 'Search across ALL files for a keyword/value when unsure which holds it.' },
  { name: 'execute_context_sql', desc: 'Run read-only SQL over a sqlite/db file (best for aggregations).' },
  { name: 'execute_universal_sql', desc: 'Run SQL over CSV/JSON files via DuckDB (native cross-file joins).' },
  { name: 'execute_python', desc: 'Run Python (pandas) over the context directory.' },
  { name: 'answer', desc: 'Submit the final answer table. Terminal action.', requiresApproval: true },
].map((t) => ({
  ...t, ...metaForTool(t.name),
  builtin: true, enabled: true, requiresApproval: !!t.requiresApproval, handler: 'python',
}));

// Map the live registry (GET /api/tools) into the UI tool shape.
export function mapServerTools(serverTools) {
  return (serverTools || []).map((t) => ({
    name: t.name,
    desc: t.description || 'Built-in tool.',
    ...metaForTool(t.name),
    builtin: true,
    enabled: true,
    requiresApproval: !!t.requires_approval,
    handler: 'python',
    inputSchema: t.input_schema,
  }));
}

// ---------------------------------------------------------------------------
// Tool "playbook" — what the agent actually DOES with a tool. Drives the small
// illustrated pipeline shown when you expand a tool in the Tools modal. Keyed by
// category (every tool has one), with optional per-tool concrete in → out demos.
// ---------------------------------------------------------------------------
const CATEGORY_PLAY = {
  list: {
    gist: 'Maps your workspace first, so the agent knows which files exist before touching them.',
    stages: [
      { icon: 'list', label: 'context/' },
      { icon: 'search', label: 'walk files', act: true },
      { icon: 'file', label: 'file tree' },
    ],
  },
  scan: {
    gist: 'Peeks inside one file — schema, types, samples — without loading the whole thing.',
    stages: [
      { icon: 'file', label: 'a file' },
      { icon: 'eye', label: 'profile', act: true },
      { icon: 'table', label: 'columns + stats' },
    ],
  },
  sql: {
    gist: 'Runs read-only SQL and streams back only the rows that match.',
    stages: [
      { icon: 'data', label: 'tables' },
      { icon: 'code', label: 'SQL', act: true },
      { icon: 'table', label: 'result rows' },
    ],
  },
  python: {
    gist: 'Runs pandas / Python in a sandbox for logic SQL can’t express.',
    stages: [
      { icon: 'code', label: 'Python' },
      { icon: 'spark', label: 'execute', act: true },
      { icon: 'table', label: 'output' },
    ],
  },
  answer: {
    gist: 'Submits the final answer table and ends the run — it’s scored against the gold answer.',
    stages: [
      { icon: 'table', label: 'result' },
      { icon: 'check', label: 'submit', act: true },
      { icon: 'spark', label: 'answer' },
    ],
  },
};

// Concrete in → out demo for the headline tools (others fall back to category).
const TOOL_EXAMPLE = {
  list_context:          { in: 'list_context(max_depth=2)', out: 'knowledge.md · events.csv · club.db' },
  profile_context:       { in: 'profile_context()', out: 'every file profiled in one call' },
  profile_csv:           { in: "profile_csv('events.csv')", out: '5 cols · types · nulls · min/max' },
  profile_quality:       { in: "profile_quality('customers.csv')", out: 'nulls · dupes · mixed types' },
  read_csv:              { in: "read_csv('events.csv')", out: 'first 5 rows preview' },
  read_doc:              { in: "read_doc('knowledge.md')", out: 'first ~8k chars of text' },
  search_doc:            { in: "search_doc('notes.md', 'budget')", out: 'top passages + context' },
  read_pdf:              { in: "read_pdf('brief.pdf')", out: 'text, page by page' },
  inspect_sqlite_schema: { in: "inspect_sqlite_schema('club.db')", out: 'tables · columns · FKs' },
  profile_database:      { in: "profile_database('club.db')", out: 'schemas · row counts · samples' },
  build_knowledge_graph: { in: 'build_knowledge_graph()', out: 'entities + join paths' },
  read_knowledge_graph:  { in: "read_knowledge_graph(query='member')", out: 'which file/table holds it' },
  map_sources:           { in: "map_sources(focus='budget')", out: 'links docs ↔ tables' },
  extract_info:          { in: "extract_info('Falcon')", out: 'every file that mentions it' },
  execute_context_sql:   { in: 'SELECT type, COUNT(*) … GROUP BY type', out: '4 rows × 2 cols' },
  execute_universal_sql: { in: "SELECT * FROM 'sales.csv' WHERE region='EU'", out: 'matching rows' },
  execute_python:        { in: "df.groupby('type').size()", out: 'aggregated table' },
  answer:                { in: 'answer(columns, rows)', out: 'final table → scored' },
};

// Resolve the visualization for one tool: category pipeline + optional demo.
export function toolPlaybook(name, category) {
  const play = CATEGORY_PLAY[category] || CATEGORY_PLAY.scan;
  return { ...play, example: TOOL_EXAMPLE[name] || null };
}

// Category → handler badge label.
export const HANDLER_LABEL = { python: 'Python', rest: 'REST endpoint', mcp: 'MCP tool' };

export const categoryOf = (name, tools) => tools.find((t) => t.name === name)?.category || 'scan';
export const toolByName = (name, tools) => tools.find((t) => t.name === name);
