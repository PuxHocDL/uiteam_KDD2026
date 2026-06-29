// =============================================================================
// Guided product tour — the end-to-end walkthrough shown on first visit (and
// re-launchable from the Sessions page). Each step points at a real element via
// its `target` (a [data-tour="…"] anchor) and is tagged with the `page` it lives
// on so the Tour engine can drive across the Sessions → Workspace navigation.
//
//   target     — data-tour key of the element to spotlight (null = centered card)
//   page       — 'sessions' | 'workspace' (which screen the step belongs to)
//   placement  — where the coach-mark sits relative to the target
//   tip        — a concrete example shown in a highlighted note (the "ví dụ mẫu")
// =============================================================================

export const TOUR_STEPS = [
  {
    id: 'welcome', page: 'sessions', target: null, placement: 'center',
    title: 'Welcome to Data Agent Studio 👋',
    body: 'Take a 60-second tour of the whole flow — from raw files to a verified answer. You can skip anytime, or replay it later from the Sessions page.',
  },
  {
    id: 'sessions', page: 'sessions', target: 'tour-sessions', placement: 'bottom',
    title: 'Sessions are your workspaces',
    body: 'Each session is isolated — its own files, conversation, agent settings and results. Switch between projects without anything bleeding across.',
  },
  {
    id: 'search', page: 'sessions', target: 'tour-search', placement: 'bottom',
    title: 'Find a past session',
    body: 'Once you have a few, search them here by name.',
    tip: 'Try names like “Q3 sales review” or “customer churn”.',
  },
  {
    id: 'new', page: 'sessions', target: 'tour-new-session', placement: 'bottom',
    title: 'Create a workspace',
    body: 'This is where you start. Click Next and we’ll open a workspace so you can see the rest of the flow — or click New Session yourself.',
    tip: 'Tip: name a session after the analysis so it’s easy to find later.',
  },
  {
    id: 'files', page: 'workspace', target: 'tour-files', placement: 'right',
    title: '1 · Add your data',
    body: 'Your uploaded files live here — CSV, Excel, JSON, SQLite, PDF or text. The agent only ever sees the data you put in this panel.',
  },
  {
    id: 'upload', page: 'workspace', target: 'tour-upload', placement: 'bottom',
    title: 'Upload or try a sample',
    body: 'Bring files from your computer, or drop in a built-in sample to start exploring immediately.',
    tip: 'No data handy? Use a sample like customers_dirty.csv to see data-quality fixes.',
  },
  {
    id: 'settings', page: 'workspace', target: 'tour-settings', placement: 'bottom',
    title: '2 · Connect a model',
    body: 'Set your model and API key here. The agent uses it to reason over your data — nothing runs until this is set.',
    tip: 'Any OpenAI-compatible endpoint works (model + base URL + key).',
  },
  {
    id: 'tools', page: 'workspace', target: 'tour-tools', placement: 'bottom',
    title: 'The agent’s toolbox',
    body: 'Every tool the agent can call lives here. Click any tool to see an illustrated example of what it does — and toggle off the ones you don’t want it to use.',
  },
  {
    id: 'mode', page: 'workspace', target: 'tour-mode', placement: 'bottom',
    title: '3 · Choose how it runs',
    body: 'Autopilot runs end-to-end while you watch. Co-pilot pauses before every step so you can approve, edit, or redirect the agent.',
  },
  {
    id: 'ask', page: 'workspace', target: 'tour-input', placement: 'top',
    title: '4 · Ask a question',
    body: 'Type a question in plain language and press Send. The agent plans, calls tools, runs SQL/Python, and returns an answer. Press / anytime to jump back to this box.',
    tip: 'Example: “Which region had the highest total sales in 2024?”',
  },
  {
    id: 'activity', page: 'workspace', target: 'tour-activity', placement: 'left',
    title: 'Watch the agent work — live',
    body: 'Open the Activity view for a real-time animation of what the agent is doing: which tool it picked, scanning your files, or running SQL / Python. It’s the friendly face of the live trace.',
    tip: 'Right now it’s idle — once you ask a question, it springs to life step by step.',
  },
  {
    id: 'plan', page: 'workspace', target: 'tour-plan', placement: 'right',
    title: 'Watch it think',
    body: 'The live plan and trace reveal every thought, tool call and result as they happen — full transparency, no black box. Click any phase to see exactly why the agent did it.',
  },
  {
    id: 'results', page: 'workspace', target: 'tour-results', placement: 'left',
    title: '5 · Get the answer',
    body: 'The final answer table, charts and the evidence behind it show up here. Copy it or export to CSV anytime — and every answer is grounded only in your data.',
  },
  {
    id: 'doctor', page: 'workspace', target: 'tour-doctor', placement: 'left',
    title: 'Bonus · Data Doctor',
    body: 'Spot and fix data-quality problems — missing values, duplicates, bad formats — right here in its own tab. It runs on its own (never clutters your chat); preview each fix, then approve to write a cleaned copy.',
    tip: 'Great first step before asking questions on messy data.',
  },
  {
    id: 'done', page: 'workspace', target: null, placement: 'center',
    title: 'You’re all set 🎉',
    body: 'That’s the full loop: add data → connect a model → ask → watch it work → get a verified answer. You can replay this tour anytime from the Sessions page.',
  },
];
