// =============================================================================
// Mock data — grounded in the ACTUAL competition task (DABench-style).
// A task = a natural-language QUESTION + a folder of context files
// (CSV / JSON / SQLite + knowledge.md). The agent explores the files, runs
// SQL/Python, and submits an ANSWER TABLE (rows × columns) scored vs gold.csv.
//
// Scenario below mirrors the real "Student Club / events" family of tasks.
// Replace with real API data when wiring the backend.
// =============================================================================

// --- Session list: each is a real DABench dataset domain ---------------------
export const SESSIONS = [
  { id: 's1', name: 'Student Club — events', domain: 'CSV · JSON', created: 'May 05, 2026, 07:51 AM', modified: 'May 05, 2026, 09:12 AM' },
  { id: 's2', name: 'Thrombosis Prediction — medical', domain: 'JSON · knowledge.md', created: 'Mar 10, 2026, 03:34 PM', modified: 'Mar 10, 2026, 03:34 PM' },
  { id: 's3', name: 'Energy Consumption — SME/LAM/KAM', domain: 'SQLite', created: 'Mar 10, 2026, 03:11 PM', modified: 'Mar 10, 2026, 03:11 PM' },
  { id: 's4', name: 'Gas Station Transactions', domain: 'SQLite', created: 'Mar 10, 2026, 10:55 AM', modified: 'Mar 10, 2026, 10:56 AM' },
  { id: 's5', name: 'Financial — Czech bank', domain: 'SQLite', created: 'Mar 05, 2026, 07:13 AM', modified: 'Mar 05, 2026, 10:13 AM' },
  { id: 's6', name: 'Atoms & Bonds — chemistry', domain: 'CSV', created: 'Mar 05, 2026, 07:11 AM', modified: 'Mar 05, 2026, 10:13 AM' },
  { id: 's7', name: 'European Football', domain: 'SQLite', created: 'Mar 05, 2026, 07:11 AM', modified: 'Mar 05, 2026, 07:12 AM' },
  { id: 's8', name: 'Auto-MPG', domain: 'CSV', created: 'Feb 25, 2026, 03:44 PM', modified: 'Feb 25, 2026, 03:44 PM' },
];

// --- The task (question + difficulty) ---------------------------------------
export const TASK = {
  task_id: 'task_145',
  difficulty: 'medium',
  question:
    'For each event type, how many events were attended by more than 10 members of the Student Club? List the event type and the count.',
};

// Question decomposition — the structured breakdown the ReAct engine enforces
// on step 1 (Entities / Filters / Aggregation / Output / Not-asking).
export const DECOMPOSITION = [
  { k: 'Entities', v: 'Events organised by the Student Club.' },
  { k: 'Filters', v: 'Keep only events with attendance > 10 members.' },
  { k: 'Aggregation', v: 'COUNT events, GROUPED BY event_type.' },
  { k: 'Output', v: 'Two columns: event_type, event_count.' },
  { k: 'Not-asking', v: 'Not counting members; not all events — only those above 10 attendees.' },
];

// Agent phases (the right-most lights up as the run progresses).
export const PHASES = ['Understand question', 'Explore context', 'Compute', 'Validate & answer'];

// ---------------------------------------------------------------------------
// Context files — WITH embedded preview/profile data so the UI can VISUALISE
// the data input (table preview + per-column profiling + histograms).
// ---------------------------------------------------------------------------
export const FILES = [
  {
    id: 'f1', name: 'knowledge.md', size: '2.1 KB', kind: 'md',
    markdown: `# Knowledge Guide — Student Club

## Entities
- **Event**: an activity organised by the club. Has an \`event_type\`.
- **Member**: a person in the club. May attend many events.
- **Attendance**: number of distinct members present at an event.

## Definitions
- \`event_type\` ∈ { Meeting, Social, Workshop, Fundraiser }.
- "attended by more than 10 members" → **attendance > 10** (strictly greater).
- An event is counted once, regardless of how many members attended.

## Metrics
- **event_count**: COUNT of events satisfying the filter, per type.`,
  },
  {
    id: 'f2', name: 'events.csv', size: '1.4 KB', kind: 'csv', rowCount: 18,
    preview: {
      columns: ['event_id', 'event_name', 'event_type', 'event_date', 'attendance'],
      rows: [
        [1, 'Spring Kickoff', 'Social', '2013-03-04', 28],
        [2, 'October Meeting', 'Meeting', '2013-10-08', 14],
        [3, 'Budget Workshop', 'Workshop', '2013-09-12', 11],
        [4, 'Annual Gala', 'Fundraiser', '2013-11-20', 42],
        [5, 'Weekly Sync', 'Meeting', '2013-05-06', 9],
        [6, 'Game Night', 'Social', '2013-06-15', 17],
        [7, 'Officer Meeting', 'Meeting', '2013-02-11', 19],
        [8, 'Welcome Mixer', 'Social', '2013-01-22', 21],
        [9, 'Resume Workshop', 'Workshop', '2013-04-18', 26],
        [10, 'Planning Meeting', 'Meeting', '2013-07-09', 15],
        [11, 'Charity Drive', 'Fundraiser', '2013-12-02', 9],
        [12, 'Town Hall', 'Meeting', '2013-08-14', 23],
        [13, 'Movie Social', 'Social', '2013-05-30', 10],
        [14, 'Coding Workshop', 'Workshop', '2013-10-25', 7],
        [15, 'Board Meeting', 'Meeting', '2013-03-19', 6],
        [16, 'Summer Social', 'Social', '2013-06-28', 5],
        [17, 'Design Workshop', 'Workshop', '2013-11-05', 8],
        [18, 'Standup Meeting', 'Meeting', '2013-09-02', 8],
      ],
    },
    profile: [
      { name: 'event_id', type: 'int', nullPct: 0, distinct: 18, min: 1, max: 18 },
      { name: 'event_name', type: 'text', nullPct: 0, distinct: 18 },
      { name: 'event_type', type: 'cat', nullPct: 0, distinct: 4, top: [
        { value: 'Meeting', count: 7 }, { value: 'Social', count: 5 }, { value: 'Workshop', count: 4 }, { value: 'Fundraiser', count: 2 },
      ] },
      { name: 'event_date', type: 'date', nullPct: 0, distinct: 18, min: '2013-01-22', max: '2013-12-02' },
      { name: 'attendance', type: 'int', nullPct: 0, distinct: 16, min: 5, max: 42, mean: 15.4, histogram: [
        { label: '≤10', count: 8 }, { label: '10–20', count: 5 }, { label: '20–30', count: 4 }, { label: '30–40', count: 0 }, { label: '40+', count: 1 },
      ] },
    ],
  },
  {
    id: 'f3', name: 'members.csv', size: '1.1 KB', kind: 'csv', rowCount: 12,
    preview: {
      columns: ['member_id', 'first_name', 'last_name', 'position', 'major', 'phone', 'status'],
      rows: [
        [101, 'Alex', 'Nguyen', 'President', 'Computer Science', '555-0101', 'active'],
        [102, 'Maria', 'Lopez', 'Treasurer', 'Economics', '555-0102', 'active'],
        [103, 'Sven', 'Olsen', 'Member', 'Mechanical Eng.', '', 'active'],
        [104, 'Aiko', 'Tanaka', 'Officer', 'Design', '555-0104', 'active'],
        [105, 'John', 'Smith', 'Member', 'Computer Science', '', 'active'],
        [106, 'Lena', 'Brandt', 'Secretary', 'Biology', '555-0106', 'active'],
        [107, 'Omar', 'Haddad', 'Member', '', '', 'active'],
        [108, 'Yuki', 'Sato', 'Member', 'Design', '555-0108', 'active'],
        [109, 'Priya', 'Patel', 'Officer', 'Economics', '', 'active'],
        [110, 'Tom', 'Becker', 'Member', 'Computer Science', '555-0110', 'active'],
        [111, 'Sara', 'Cohen', 'Member', '', '', 'active'],
        [112, 'Ravi', 'Iyer', 'Member', 'Biology', '555-0112', 'active'],
      ],
    },
    profile: [
      { name: 'member_id', type: 'int', nullPct: 0, distinct: 12, min: 101, max: 112 },
      { name: 'first_name', type: 'text', nullPct: 0, distinct: 12 },
      { name: 'last_name', type: 'text', nullPct: 0, distinct: 12 },
      { name: 'position', type: 'cat', nullPct: 0, distinct: 4, top: [
        { value: 'Member', count: 7 }, { value: 'Officer', count: 2 }, { value: 'President', count: 1 }, { value: 'Treasurer', count: 1 },
      ] },
      { name: 'major', type: 'cat', nullPct: 17, distinct: 4, top: [
        { value: 'Computer Science', count: 3 }, { value: 'Design', count: 2 }, { value: 'Economics', count: 2 }, { value: 'Biology', count: 2 },
      ] },
      { name: 'phone', type: 'text', nullPct: 42, distinct: 7 },
      { name: 'status', type: 'cat', nullPct: 0, distinct: 1, top: [{ value: 'active', count: 12 }] },
    ],
  },
  {
    id: 'f4', name: 'budgets.json', size: '0.4 KB', kind: 'json',
    json: [
      { event_id: 2, category: 'catering', budget: 120.0, approved: true },
      { event_id: 3, category: 'materials', budget: 75.5, approved: true },
      { event_id: 4, category: 'venue', budget: 900.0, approved: true },
    ],
  },
  {
    id: 'f5', name: 'club.db', size: '48 KB', kind: 'sqlite',
    tables: [
      {
        name: 'attendance', rowCount: 142,
        columns: [
          { name: 'event_id', type: 'INTEGER', fk: 'events.event_id' },
          { name: 'member_id', type: 'INTEGER', fk: 'members.member_id' },
          { name: 'checked_in', type: 'TEXT' },
        ],
        sample: [[1, 101, 'Y'], [1, 105, 'Y'], [2, 102, 'N'], [4, 110, 'Y']],
      },
      {
        name: 'event_log', rowCount: 60,
        columns: [
          { name: 'log_id', type: 'INTEGER', pk: true },
          { name: 'event_id', type: 'INTEGER', fk: 'events.event_id' },
          { name: 'note', type: 'TEXT' },
        ],
        sample: [[1, 1, 'venue booked'], [2, 4, 'catering confirmed']],
      },
    ],
  },
];

// Output of build_knowledge_graph — entities (files/tables) + join paths (FKs).
// Positions are % within the ER stage; the graph component draws the edges.
export const KNOWLEDGE_GRAPH = {
  entities: [
    { id: 'events', label: 'events', source: 'events.csv', x: 50, y: 20, columns: [
      { name: 'event_id', key: 'pk' }, { name: 'event_type' }, { name: 'event_date' }, { name: 'attendance' },
    ] },
    { id: 'members', label: 'members', source: 'members.csv', x: 17, y: 74, columns: [
      { name: 'member_id', key: 'pk' }, { name: 'first_name' }, { name: 'position' }, { name: 'major' },
    ] },
    { id: 'attendance', label: 'attendance', source: 'club.db', x: 50, y: 76, columns: [
      { name: 'event_id', key: 'fk' }, { name: 'member_id', key: 'fk' }, { name: 'checked_in' },
    ] },
    { id: 'budgets', label: 'budgets', source: 'budgets.json', x: 83, y: 74, columns: [
      { name: 'event_id', key: 'fk' }, { name: 'category' }, { name: 'budget' },
    ] },
  ],
  joins: [
    { from: 'attendance', to: 'events', on: 'event_id' },
    { from: 'attendance', to: 'members', on: 'member_id' },
    { from: 'budgets', to: 'events', on: 'event_id' },
  ],
};

// ---------------------------------------------------------------------------
// Scripted run — uses the REAL Phase-1 tools (list_context, read_doc,
// profile_csv, execute_universal_sql, answer). DuckDB queries CSV/JSON directly.
// ---------------------------------------------------------------------------
export const SAMPLE_QUESTION = TASK.question;

export const SCRIPTED_RUN = {
  question: TASK.question,
  steps: [
    {
      thought:
        'Decompose → Entities: events. Filters: attendance > 10. Aggregation: COUNT grouped by event_type. ' +
        'Output: [event_type, event_count]. Not-asking: not member counts, not all events.',
      action: 'list_context',
      actionInput: { max_depth: 2 },
      reason: 'Always map the context folder first to see which files exist.',
      durationMs: 800, ok: true,
      observation: { summary: 'Found knowledge.md, events.csv, members.csv, budgets.json.' },
      details: { files: ['knowledge.md', 'events.csv', 'members.csv', 'budgets.json'] },
    },
    {
      thought: 'Read knowledge.md to pin down definitions before querying.',
      action: 'read_doc',
      actionInput: { path: 'knowledge.md', max_chars: 8000 },
      reason: 'It defines event_type values and that "more than 10" means attendance > 10 (strict).',
      durationMs: 900, ok: true,
      observation: { summary: 'event_type ∈ {Meeting, Social, Workshop, Fundraiser}; filter = attendance > 10; count each event once.' },
      details: { event_types: ['Meeting', 'Social', 'Workshop', 'Fundraiser'], filter: 'attendance > 10' },
    },
    {
      thought: 'Profile events.csv to confirm column names/types before writing SQL.',
      action: 'profile_csv',
      actionInput: { path: 'events.csv' },
      reason: 'Avoid guessing column names — confirm event_type and attendance exist.',
      durationMs: 850, ok: true,
      observation: { summary: '18 rows. attendance: int (min 3, max 42). event_type: 4 categories.' },
      details: { rows: 18, columns: ['event_id', 'event_name', 'event_type', 'event_date', 'attendance'] },
      produces: { stage: 'Exploration', artifact: 'events.csv · profile' },
    },
    {
      thought: 'Compute the grouped count with one DuckDB query over the CSV.',
      action: 'execute_universal_sql',
      actionInput: {
        sql: "SELECT event_type, COUNT(*) AS event_count FROM 'events.csv' WHERE attendance > 10 GROUP BY event_type ORDER BY event_count DESC",
        limit: 500,
      },
      reason: 'DuckDB can query the CSV directly — no Python needed for a simple GROUP BY.',
      durationMs: 1200, ok: true,
      observation: { summary: '4 rows → Meeting 4, Social 3, Workshop 2, Fundraiser 1.' },
      details: { rows: 4, sql_ok: true },
      produces: { stage: 'Computation', artifact: 'result.csv' },
      producesChart: true,
    },
    {
      thought: 'Result is clean and matches the requested two columns. Submit it as the final answer.',
      action: 'answer',
      actionInput: { columns: ['event_type', 'event_count'], rows: 4 },
      reason: 'Submitting the final answer table — please confirm it is correct.',
      requiresApproval: true, // a sensible HITL gate for a data agent: confirm before final submit
      durationMs: 600, ok: true,
      observation: { summary: 'Submitted answer: 4 rows × 2 columns.' },
      kind: 'answer',
    },
  ],
  finalSummary:
    'Four event types had events attended by more than 10 members: **Meeting** (4 events), **Social** (3), ' +
    '**Workshop** (2), and **Fundraiser** (1). The answer table below matches the requested columns ' +
    '[event_type, event_count].',
};

// Final answer table (compared against gold.csv in the real benchmark).
export const ANSWER_TABLE = {
  columns: ['event_type', 'event_count'],
  numeric: { event_count: true },
  rows: [
    ['Meeting', '4'],
    ['Social', '3'],
    ['Workshop', '2'],
    ['Fundraiser', '1'],
  ],
};

// Chart derived from the answer (data-agent results are often groupable).
export const CHART = {
  title: 'Events with > 10 attendees, by type',
  unit: 'events',
  bars: [
    { label: 'Meeting', value: 4 },
    { label: 'Social', value: 3 },
    { label: 'Workshop', value: 2 },
    { label: 'Fundraiser', value: 1 },
  ],
};
