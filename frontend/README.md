# Data Agent Studio — Frontend

A React + Vite **chatbot UI** for the Phase-2 Data Agent Studio, grounded in the
**actual competition task** (DABench-style data agent) and themed with a clean, **dark-first**
palette. It runs in two ways:

- **Demo** — a scripted run replayed by a timer engine (`src/hooks/useAgentRun.js`,
  `src/data/mockData.js`). No backend needed; great for showing the UX.
- **Live** — drives the **real Phase-1 agent** through the FastAPI gateway
  ([`server/app.py`](../server/app.py)) over SSE, in **both** Autopilot and Co-pilot.

A task = a natural-language **question** + a folder of **context files**
(CSV / JSON / SQLite + `knowledge.md`). The agent explores the files, runs SQL/Python,
and submits an **answer table** (rows × columns). The UI lets an end user **follow every
step** and (in Co-pilot) **intervene** before each tool runs.

## Run

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173   (Demo works with no backend)
npm run build      # production bundle in dist/
```

## Run LIVE (real agent)

1. **Start the backend** from the repo root:
   ```bash
   uv run uvicorn server.app:app --reload --port 8000
   ```
2. In the UI open **Agent Settings** (gear) → pick an endpoint preset (Azure OpenAI /
   OpenAI / OpenRouter / local) and fill **model / API base / API key / API version**.
3. In the chat header flip **Demo → Live** (the dot shows backend health), pick a
   **real task**, type a question, and send.

**Both modes work live now:** Autopilot streams end-to-end; **Co-pilot** pauses on
`AWAITING_USER` before each tool and sends **approve / edit / reject / guide / cancel**
back via `POST /api/decide`. Backend URL is configurable in
[`src/lib/api.js`](src/lib/api.js) (or `localStorage['das-api']`). Keys are sent only to
your local backend — never commit them.

## AI data fixes — in the Results panel (live)

There is no separate modal — the **AI data fixes** section lives at the bottom of the
**Results panel** ([`DataFixes.jsx`](src/components/workspace/DataFixes.jsx)). Pick an
uploaded **CSV/Excel** file from its dropdown and the **LLM** reviews it:

1. The backend builds a **factual profile** (type, null %, unique, samples, stats) and the
   **LLM finds the issues + recommends fixes** — not hard-coded rules, so it scales beyond a
   fixed checklist (`POST /api/sessions/{id}/quality`, needs your model key in Settings).
2. Each recommendation is a card. **Fix** runs a **dry-run preview** — an animated
   **before → after** diff (writes nothing); **Approve** commits it to a **`*_clean.csv`**
   (the original is never touched); **Edit** tweaks params; **Skip** dismisses. Fixes chain
   on the cleaned copy; **Download** grabs the result.

The fix *actions* themselves (impute / trim / normalise case / to-number / to-date /
drop dup-rows / drop / rename) stay deterministic in
[`src/data_agent_baseline/tools/data_quality.py`](../src/data_agent_baseline/tools/data_quality.py)
(`apply_fix`) — the LLM only chooses among them. Try it with the sample
[`assets/samples/customers_dirty.csv`](../assets/samples/customers_dirty.csv).

## 📊 Explore (data-scientist statistics) — live

The **chart icon** in the Files panel opens **Explore** ([`ExploreModal.jsx`](src/components/workspace/ExploreModal.jsx)) —
a deterministic statistical view of a CSV/Excel file (shares the workspace session, so
the same uploads are reused):

- **Columns** — each column's distribution: a **histogram** + min/median/max/μ for numeric,
  **top categories** for categorical, range for datetime, with missing/outlier/unique counts.
- **Correlation** — a Pearson **correlation heatmap** over numeric columns + the strongest
  related pairs.
- **Missingness** — a per-column missing-percentage map.

Excel files get a **sheet picker** (each sheet = one table). Stats come from
[`tools/explore.py`](../src/data_agent_baseline/tools/explore.py) behind
`POST /api/sessions/{id}/explore`; charts are plain SVG/CSS (no chart library).

## What's in the UI

- **Session grid** — polished cards (search, New Session). *(Still demo data — real
  session CRUD is roadmap §12.3.)*
- **Agent Settings** (gear) — pick the **LLM endpoint** (model / base / key / temperature
  / max steps, with presets) and the **solution**: **ReAct / DRAGIN / Multi-agent /
  Hybrid-B** (+ optional Consensus). See `src/data/agentOptions.js`.
- **Workspace** (3 panes):
  - **Files + Plan** (left): **real files** on the workspace session — upload (drag-drop /
    browse), delete, and **eye → live preview** (sortable table for CSV/Excel, text for
    JSON/MD). Plus **🤖 analyze with agent** (runs the engine over your uploads),
    **🤖 analyze with agent**, **📊 Explore**, **Relationships** and **Search** (AI data
    fixes live in the Results panel). **Plan** shows the
    agent's live **question decomposition** + phase tracker (Understand → Explore → Compute →
    Answer). *(Relationships & Search still use demo data — roadmap §12.2b.)*
  - **Conversation** (center): **Autopilot / Co-pilot** toggle, a streaming **Thought →
    Action → Observation** trace using the real Phase-1 tools, and a **step-approval card**
    (Approve / Edit / Reject / Guide / Cancel) when paused.
  - **Results** (right): **Activity** (live tool-dock animation), **Answer table**,
    **Chart**, **Event Log**.
- **Tools** (chip in the chat header) — inspect and **register** tools (name, schema,
  handler = Python / REST / MCP, requires-approval): the no-code face of the plug-and-play
  Tool Registry. See `src/data/tools.js` + `ToolsModal.jsx`.

### Live vs demo at a glance

| Area | Status |
|---|---|
| Conversation run (Autopilot **and** Co-pilot) | **Live** via `useAgentRunLive` + SSE |
| Files panel — list · upload · delete · preview | **Live** on a real workspace session (§12.4) |
| Agent over **your uploads** (🤖 in Files) | **Live** — `useAgentRunLive` sends `session_id`; the agent profiles/cleans with its tools + LLM |
| AI data fixes (in the Results panel) | **Live** — LLM finds issues, preview→approve, deterministic apply |
| Explore (distributions · correlation · missingness) | **Live** via session/explore endpoint |
| Session list (multiple sessions), Relationships, Search | Demo data (§12.3 / §12.2b pending) |

## Theming + dark mode

All colors live in [`src/styles/theme.css`](src/styles/theme.css) as CSS variables — edit
the tokens to match exact brand hex. The **default is
dark** — near-black surfaces, white text, and a **turquoise `#50DCE1`**
(`--ds-teal-400`) accent. A clean **light** mode is the toggle (`:root` holds the light
tokens, `[data-theme="dark"]` the dark default), switched from the top bar and persisted
to `localStorage`. A `prefers-reduced-motion` fallback disables the animation-heavy
activity stage for users who ask for it.

## Folder map

```
src/
  lib/
    eventContract.js        # EVENT / COMMAND / RUN_STATE — backend↔frontend contract
    api.js                  # live client: runStream(SSE), decide, chat, sessions, files, Data Doctor
    chat.js                 # greeting/small-talk intent classifier
  data/
    mockData.js             # demo sessions, files, scripted run, answer, chart
    agentOptions.js         # endpoints + solutions (ReAct/DRAGIN/Multi/Hybrid-B) + settings persistence
    tools.js                # built-in tool catalogue for ToolsModal
  hooks/
    useAgentRun.js          # DEMO timer engine (replays a scripted run)
    useAgentRunLive.js      # LIVE engine: SSE trace + co-pilot decisions (same state shape)
  components/
    common/                 # TopBar, ModeToggle, Modal, RichText, Icons
    sessions/SessionList.jsx
    workspace/
      Workspace.jsx         # composes the 3 panes + modals; Demo/Live switch
      FilesPanel · PlanPanel · ChatPanel · ResultsPanel
      TraceTimeline · CoPilotCard · AgentActivity · BarChart
      ResultsPanel + DataFixes.jsx   # ← §12.1 AI data fixes (LLM issues + apply, in Results)
      ExploreModal.jsx      # ← §12.2c statistics: histograms / correlation / missingness (live)
      DataPreviewModal · RelationshipGraph · DataSearchModal
      SettingsModal · ToolsModal · UploadModal
  styles/theme.css · app.css
```

## Backend contract & roadmap

Both hooks return the **same shape** (`feed`, `events`, `results`, `busy`, `plan`,
`send/approve/editAndRun/guide/reject/cancel`), so components never change between Demo and
Live. The wire contract is `lib/eventContract.js` (`EVENT.*` in, `COMMAND.*` out) — the
single event/command source of truth shared by backend and frontend. Implemented features
include Data Doctor, deep visualization, session/file CRUD, multi-solution recommendation,
and plan/evidence drill-down.
