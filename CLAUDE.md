# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Data Agent Studio** (Team UIT, KDD Cup 2026 — Creative Track): a transparent, controllable chatbot
wrapper around a real data-analysis agent. The same engine that powers the interactive Studio also runs
as the competition's DataAgent-Bench CLI (`dabench`) — there is no separate "demo" engine.

## Commands

### Backend / engine (Python, managed with `uv`)

```bash
uv sync                                             # install deps (see pyproject.toml)
uv run uvicorn server.app:app --port 8000           # run the FastAPI gateway (Studio backend)
uv run pytest                                       # run full test suite
uv run pytest tests/test_reasoning.py               # run a single test module
uv run pytest tests/test_reasoning.py::test_name -v # run a single test function
uv run ruff check .                                 # lint (line-length 100, py310, src+tests)
```

Benchmark CLI (`dabench`, entry point defined in `pyproject.toml`):

```bash
cp configs/hybrid_b_baseline.example.yaml configs/hybrid_b.yaml   # add your own API key; real configs are git-ignored
uv run dabench status        --config configs/hybrid_b.yaml       # sanity-check dataset paths
uv run dabench run-benchmark --config configs/hybrid_b.yaml       # run the agent over the benchmark
uv run dabench eval                                                # score predictions vs. gold
uv run dabench run-consensus                                       # self-consistency voting across runs
```

### Frontend (React + Vite, in `frontend/`)

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173 — Demo mode needs no backend/key
npm run build
npm run preview
```

No frontend test suite or chart library exists yet — charts are hand-rolled SVG/CSS.

## Architecture

Four layers, connected by two stable contracts (an SSE event stream and a `/api/decide` command
channel) so the UI, gateway, and engine can vary independently:

```
frontend/ (React/Vite chat UI)
   ⇅ SSE events / POST /api/decide
server/app.py (FastAPI gateway: /api/run stream, /api/decide, sessions, quality, explore, text-kg, auth)
   ⇅ AgentEngine.run(...) / proceed-edit-stop
src/data_agent_baseline/ (orchestration: Runner → Hybrid-B router → {ReAct, Multi-agent, DRAGIN} + InteractionController + Memory)
   ⇅ tool calls                      ⇅ model / data
Tool Registry (YAML, tools/registry.py)     Endpoint adapters (OpenAI-compatible/Azure LLMs, SQLite, DuckDB, CSV/JSON/Excel/PDF)
```

Key modules under `src/data_agent_baseline/`:

- `run/runner.py` — entry point for single-task / benchmark / consensus runs; implements Hybrid-B
  difficulty routing (ReAct for easy/medium, DRAGIN for extreme, heuristics for hard).
- `agents/react.py` — the core ReAct loop (parse → validate → dispatch → observe → loop), with
  loop/stagnation detection, error classification with recovery hints, and a forced-final-answer path.
  This exact loop is what's evaluated on DataAgent-Bench.
- `agents/orchestrator.py` — multi-agent mode: a Planner explores, then an Analyst executes.
- `agents/dragin.py` — DRAGIN: dynamic, information-need-triggered retrieval (RIND trigger + QFS query
  formulation), used for hard/extreme-difficulty tasks.
- `agents/memory.py` — cross-run hint store (persists unverified hints only, not ground truth).
- `agents/model.py` — LLM adapters (OpenAI-compatible / Azure) with retry + exponential backoff.
- `tools/registry.py` — where every tool is registered; tools are read-only by default, and any
  write/IO tool must be flagged `requires_approval` (this flag is what triggers a Co-pilot pause and
  what the safety layer checks before executing). **Adding a tool = a registry/YAML entry, no core
  edits** — it appears automatically in the UI's Tools panel and the agent's prompt.
- `tools/{sqlite,duckdb_exec,python_exec,filesystem,explore,data_quality,knowledge_graph,text_kg}.py` —
  tool implementations: SQL (SQLite + DuckDB over CSV/JSON with cross-file JOINs), sandboxed Python
  exec (confined to the task's `context/` dir), CSV/JSON/DB profiling, tabular/text knowledge graphs.
- `benchmark/{dataset,scoring,schema}.py` — DataAgent-Bench dataset loading and scoring (value-overlap
  column matching with a λ-penalty for extra columns).

### The two interaction modes

Both flow through one `InteractionController` state machine — this is the piece to touch if you're
changing when/whether execution pauses; the engine and UI should never hard-code pause logic themselves.

- **Autopilot** — runs continuously; user watches the live trace. Still pauses for any tool flagged
  `requires_approval`.
- **Co-pilot** — after the engine proposes a step (`thought` + `action` + `action_input`) but *before*
  executing it, the run enters `AWAITING_USER`. The user can Approve / Edit / Reject / Guide / Cancel;
  decisions are sent back via `POST /api/decide`.

Everything the frontend renders comes from one normalized SSE event stream, so the UI behaves
identically regardless of which engine mode (ReAct / multi-agent / DRAGIN) is actually running.

### Configuration

Real configs (containing API keys) are git-ignored. Always start from a template:
`configs/{react,dragin,hybrid_b}_baseline.example.yaml` → copy to e.g. `configs/hybrid_b.yaml`. Modes:
`single`/`react`, `multi` (Planner→Analyst), `dragin`, `hybrid_b` (routes by difficulty).

### Safety invariants

- Ground-truth files are blocked at the tool layer (raises `PermissionError`) — do not weaken this when
  touching `tools/`.
- Write/IO tools must stay `requires_approval` in `tools/registry.py`.
- Auth is salted PBKDF2 + HMAC-signed token (`server/`); API keys are never committed
  (`*.key`, `configs/*.yaml`, `.env` are git-ignored) and only ever sent to the user's local backend.

### Diagrams

`architecture.html` is auto-generated from source by CI (`.github/workflows/architecture.yml`) — don't
hand-edit it; regenerate instead if it drifts.
