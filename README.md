<div align="center">

# Data Agent Studio

**Turn any data agent into a transparent, controllable chatbot.**

Team UIT · KDD Cup 2026 — Data Agents Competition · **Creative Track**

English | [中文](README.zh.md)

[![Track](https://img.shields.io/badge/KDD%20Cup%202026-Creative%20Track-7c3aed?style=for-the-badge)](https://dataagent.top)
[![Official Website](https://img.shields.io/badge/Official-dataagent.top-0ea5e9?style=for-the-badge&logo=googlechrome&logoColor=white&labelColor=0f172a)](https://dataagent.top)

</div>

> **Data Agent Studio** lets a non-technical user *converse with their data*. They ask a question in
> natural language; an agent plans, understands the data, calls tools, runs analysis, and answers —
> and the user **watches every step in real time** and (optionally) **steps in at each step**. The
> same system runs the competition's **DataAgent-Bench** engine, so the agent under the hood is a
> strong, evaluated data agent — not a demo.

---

## Contents

1. [The problem](#1-the-problem) ·
2. [What it is](#2-what-data-agent-studio-is) ·
3. [Key innovations](#3-key-innovations) ·
4. [Two interaction modes](#4-two-interaction-modes) ·
5. [Agent capability](#5-agent-capability--perceive--decide--act--correct) ·
6. [Architecture](#6-architecture) ·
7. [Run it](#7-run-it) ·
8. [Tools](#8-tools-the-agent-can-use) ·
9. [Reliability, cost & safety](#9-reliability-cost--safety) ·
10. [Reproducibility & evaluation](#10-reproducibility--evaluation) ·
11. [Configuration](#11-configuration-reference) ·
12. [Repository layout](#12-repository-layout) ·
13. [Tech stack & disclosure](#13-tech-stack--disclosure)

---

## 1. The problem

Most data agents — even benchmark-winning ones — are **black boxes that run in batch**: you type a
command, wait, and get a result. You can't see *what the agent thought*, *which tool it called*, or
*why it produced that answer*; you can't pause and redirect it; you can't swap the tool or the model
without editing code.

For the people who actually do data work — **analysts, decision-makers, domain experts who are not
engineers** — that makes an agent **hard to trust and hard to use**, no matter how accurate it is. A
wrong number with no visible reasoning is worse than useless; a correct one you can't explain is hard
to act on.

**Data Agent Studio targets that gap:** keep the analytical power of a real data agent, but wrap it in
a chatbot that ordinary users can *follow, trust, and control*.

## 2. What Data Agent Studio is

A **framework + chatbot** with two design pillars:

- **Plug-and-play** — tools, endpoints (LLM & data sources), *and the agent engine itself* are swapped
  by configuration, not by editing core code. Add a tool by dropping a YAML entry; add an engine by
  implementing one interface.
- **End-user-first** — a familiar, multi-turn chat UI where the agent's full reasoning is visible and
  the user chooses how much autonomy to give it.

Everything the user sees comes from **one normalized event stream**, so the UI renders the same live
trace regardless of which engine is running underneath.

## 3. Key innovations

| # | Innovation | Why it matters (Creative Track) |
|---|---|---|
| **1** | **Two interaction modes** — *Autopilot* (watch the live trace) and *Co-pilot* (approve / edit / reject / guide / cancel **every step**), driven by a single **Interaction Controller** state machine. | Controllability + trust. The mode lives in *one* place; engine and UI never hard-code it. |
| **2** | **Full transparency** — every thought, tool call, observation, and state transition is streamed (SSE) and shown as a friendly *Thought → Action → Observation* trace, with an **evidence / "how this answer was computed"** drill-down. | No invisible behavior; users can verify claims. |
| **3** | **Interface-first, plug-and-play** — `AgentEngine`, `ToolRegistry` (YAML), `ModelProvider`, and the event/command contract are stable interfaces. | Generality — not hard-coded to one algorithm, provider, or dataset. |
| **4** | **A strong, *generalizing* engine** — ReAct + multi-agent (Planner→Analyst) + **DRAGIN** (dynamic, information-need-triggered retrieval) + **Hybrid-B** difficulty routing that adapts the strategy to the task, not the dataset. | Principled, adaptive — not a single-dataset pipeline. |
| **5** | **Self-correction & verification** — loop/stagnation detection, error classification with targeted recovery hints, anti-pattern guards, a pre-answer checklist, forced-final-answer, and cross-run memory. | "Perceive → decide → act → correct," the Creative Track's definition of a real Data Agent. |
| **6** | **End-user data workflows** — **Data Doctor** (LLM finds data-quality issues → preview → human-approved deterministic fix), **Explore** (distributions / correlation / missingness), **Knowledge Graph** (tabular ER + text/PDF entities), over CSV / JSON / SQLite / Excel / PDF / docs. | Real, practically usable analytical results across heterogeneous sources. |

## 4. Two interaction modes

Same engine, same event stream — they differ only in **how far the user reaches in**.

- **① Autopilot (end-to-end).** The agent runs continuously; the user watches a **live trace** unfold
  and gets the answer plus a plain-language summary of what the agent did. Optimized for *speed +
  transparency*. (It still pauses for any tool flagged `requires_approval`.)
- **② Co-pilot (step-by-step).** After the agent *proposes* a step (`thought` + `action` +
  `action_input`) but **before it executes**, the run pauses (`AWAITING_USER`). The user can
  **Approve / Edit (e.g. tweak the SQL) / Reject / Guide (drop a hint) / Cancel**. Optimized for
  *control + trust*.

Both modes go through one `InteractionController`; commands flow back over `POST /api/decide`. Adding a
new policy (e.g. "only pause before writes") is a controller change — the engine and UI don't move.

## 5. Agent capability — perceive → decide → act → correct

The engine is a genuine agent loop, not a single LLM call:

- **Autonomous planning** — decomposes the question, plans multi-step analysis, and (in multi-agent
  mode) a Planner explores before an Analyst executes.
- **Data understanding & tool selection** — profiles files, inspects DB schemas, and picks SQL vs.
  Python vs. document search based on the data and the question.
- **Multi-step reasoning with feedback** — each observation feeds the next step; the agent adjusts when
  results don't match the question (e.g. row-count sanity checks).
- **Error recognition & verification** — classifies tool/parse errors and injects targeted fixes
  ("fix the error, don't flee it"), detects repeated/fruitless actions, runs a pre-answer checklist,
  and commits a best answer rather than failing silently.
- **Adaptation** — **Hybrid-B** routes by difficulty + content signals (`multi_source`, `sampled_db`,
  `long_doc`, …) to ReAct / DRAGIN / multi-agent, so behavior adapts to unseen data shapes.

## 6. Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Conversational UI  (frontend/ — React + Vite)        transparency · UX     │
│  multi-turn chat · Autopilot/Co-pilot toggle · live Thought→Action→Observe  │
│  trace · Plan panel · Co-pilot approve/edit/reject/guide · Data Doctor ·     │
│  Explore · Relationship graph · Results + evidence                          │
└───────────────▲─────────────────────────────────────────┬──────────────────┘
        events  │  SSE (one event contract)                │ commands (/api/decide)
┌───────────────┴─────────────────────────────────────────▼──────────────────┐
│  Gateway  (server/app.py — FastAPI)                                         │
│  /api/run (stream) · /api/decide · sessions & files · quality · explore ·   │
│  text-kg · recommend-solution · auth (hashed pw + signed token)             │
└───────────────▲─────────────────────────────────────────┬──────────────────┘
                │  AgentEngine.run(...)                     │ proceed / edit / stop
┌───────────────┴─────────────────────────────────────────▼──────────────────┐
│  Orchestration  (src/data_agent_baseline/)                                  │
│   Runner → Hybrid-B router → { ReAct · Multi-agent (Planner→Analyst) ·      │
│   DRAGIN } + Interaction Controller + cross-run Memory                      │
└─────────┬─────────────────────────────────┬─────────────────────────────────┘
          │ tools                           │ model / data
┌─────────┴───────────────┐   ┌─────────────┴───────────────────────────────┐
│ Tool Registry (YAML)     │   │ Endpoint adapters                            │
│ SQL (SQLite/DuckDB) ·     │   │ OpenAI-compatible / Azure LLMs · SQLite ·    │
│ Python · profiling · doc  │   │ DuckDB · CSV/JSON/Excel/PDF/docs             │
│ search (BM25) · KG · …    │   │                                              │
└──────────────────────────┘   └──────────────────────────────────────────────┘
```

The engine's per-step ReAct loop (parse → validate → dispatch → observe → loop, with loop/regex
recovery hints and a forced final answer) is the same one evaluated on DataAgent-Bench.

## 7. Run it

There are two front doors — the **interactive Studio** (the product) and the **benchmark CLI** (the
engine in batch). The chatbot also has an offline **Demo** mode that needs no backend.

### A) Interactive Studio (chatbot)

```bash
# 1) Backend (FastAPI gateway)
uv sync
uv run uvicorn server.app:app --port 8000

# 2) Frontend (in a second terminal)
cd frontend
npm install
npm run dev          # http://localhost:5173
```

In the UI: open **Agent Settings** → pick an LLM endpoint (OpenAI-compatible / Azure / local) and enter
your **model / API base / API key**; flip **Demo → Live**; choose **Autopilot** or **Co-pilot**; upload
data and ask. Keys are sent only to your local backend and are never committed.

### B) Benchmark engine (CLI)

```bash
uv sync
cp configs/hybrid_b_baseline.example.yaml configs/hybrid_b.yaml   # then add your API key
uv run dabench status        --config configs/hybrid_b.yaml       # sanity check paths
uv run dabench run-benchmark --config configs/hybrid_b.yaml       # run the agent on the dataset
uv run dabench eval                                                # score predictions vs. gold
```

### Demo (no backend, no key)

```bash
cd frontend && npm install && npm run dev   # the chat replays a scripted run to show the UX
```

## 8. Tools the agent can use

Tools are registered in `src/data_agent_baseline/tools/registry.py` (read-only by default; write/IO
tools are flagged `requires_approval`). Grouped by capability:

| Group | Tools |
|---|---|
| **Explore** | `list_context`, `read_csv`, `profile_csv`, `read_json`, `profile_json`, `profile_database`, `inspect_sqlite_schema` |
| **Documents** | `read_doc`, `read_doc_chunk`, `search_doc` (BM25 / regex), `read_pdf` |
| **SQL** | `execute_context_sql` (SQLite), `execute_universal_sql` (DuckDB over CSV/JSON, cross-file JOINs) |
| **Compute** | `execute_python` (sandboxed in the task `context/`) |
| **Knowledge** | `build_knowledge_graph` / `read_knowledge_graph` (tabular ER), text/PDF entity graph, `map_sources` |
| **Data quality** | `profile_quality` (+ the Studio's Data Doctor: LLM-suggested, human-approved deterministic fixes) |
| **Planning / control** | `classify_question`, `plan_task`, `answer` (terminal) |

Adding a tool is a registry/YAML change — no core edits — and it immediately appears in the UI's Tools
panel and the agent's prompt.

## 9. Reliability, cost & safety

- **Cost / latency by design** — difficulty routing keeps cheap paths cheap (ReAct for easy/medium) and
  reserves heavier strategies (DRAGIN / multi-agent) for hard tasks; `max_steps`, `temperature`, and
  step-budget warnings cap spend. *A bigger model is not the strategy* — routing and self-correction are.
- **Robustness** — exponential-backoff retry on transient LLM errors, per-task timeouts, hard loop/error
  breakers, tolerant JSON parsing (`<think>` tags, string inputs, unbalanced brackets), and explicit
  UTF-8 I/O.
- **Safety / privacy** — write/IO tools require approval; ground-truth files are blocked at the tool
  layer (`PermissionError`); auth uses salted PBKDF2 + an HMAC-signed token; API keys stay on the user's
  local backend and are never committed (`*.key`, `configs/*.yaml`, `.env` are git-ignored).
- **Scalability** — benchmark runs parallelize across tasks with a thread pool.

## 10. Reproducibility & evaluation

- **Tests** — `uv run pytest` (9 self-contained modules in `tests/`; in-memory + `tmp_path` fixtures,
  no external data needed).
- **Eval harness** — `dabench eval` scores predictions against gold (value-overlap column matching with
  a λ-penalty for extra columns); `run-consensus` does self-consistency voting across runs.
- **Sample data** — `assets/samples/` ships dirty CSVs, multi-sheet-style tables, linked SQLite DBs
  (`crm.db` ↔ `billing.db`), and PDFs to exercise Data Doctor, Explore, and the Knowledge Graph.
- **Container** — `image code/Dockerfile` builds a runnable image of the engine.
- **Diagrams** — `architecture.html` (auto-generated from source via CI) renders the live module and
  call graphs.

## 11. Configuration reference

Real configs are git-ignored so keys never commit — copy a shipped template
(`configs/{react,dragin,hybrid_b}_baseline.example.yaml`) to e.g. `configs/hybrid_b.yaml` and add your
key.

```yaml
agent:
  model: YOUR_MODEL_NAME
  api_base: YOUR_API_BASE_URL        # OpenAI-compatible
  api_key: YOUR_API_KEY              # never commit a real key
  agent_mode: hybrid_b               # single/react | multi | dragin | hybrid_b
  max_steps: 20
  temperature: 0.0
  dragin_rind_threshold: 0.28        # higher = retrieve less often
  hybrid_hard_min_signals: 2         # signals before a hard task routes to DRAGIN
run:
  output_dir: artifacts/runs
  max_workers: 18
  task_timeout_seconds: 1800
```

| Mode | Behavior |
|---|---|
| `single` / `react` | ReAct loop with self-correction |
| `multi` | Planner (explore) → Analyst (execute) |
| `dragin` | DRAGIN-style dynamic, need-triggered retrieval |
| `hybrid_b` | ReAct for easy/medium, DRAGIN for extreme, heuristic routing for complex hard tasks |

## 12. Repository layout

```
frontend/                      React + Vite chatbot (trace, Co-pilot, Data Doctor, Explore, graph)
server/                        FastAPI gateway: SSE run, /api/decide, sessions/files, quality, auth
src/data_agent_baseline/
  run/runner.py                single-task / benchmark / consensus + Hybrid-B routing
  agents/react.py              ReAct loop: loop/error detection, recovery hints, forced answer
  agents/orchestrator.py       multi-agent Planner → Analyst
  agents/dragin.py             DRAGIN dynamic retrieval (RIND trigger + QFS query)
  agents/memory.py             cross-run hint store (unverified hints only)
  agents/model.py              LLM adapters (OpenAI / Azure) with retry + backoff
  tools/registry.py            tool registration / dispatch (read-only + requires_approval)
  tools/{sqlite,duckdb_exec,python_exec,filesystem,explore,data_quality,knowledge_graph,text_kg}.py
  benchmark/{dataset,scoring,schema}.py
configs/                       example configs (real ones git-ignored)
tests/                         pytest suite
assets/samples/                sample datasets for the demos
image code/Dockerfile          containerized engine
```

## 13. Tech stack & disclosure

- **Backend / engine** — Python, FastAPI, Pydantic, pandas, DuckDB, SQLite; OpenAI-compatible / Azure
  LLM clients behind adapters.
- **Frontend** — React + Vite; charts are hand-rolled SVG/CSS (no chart library).
- **Models** — any OpenAI-compatible chat model (configured at runtime; no model weights are shipped).
- **Data** — the public DataAgent-Bench demo set (not redistributed here) plus synthetic samples in
  `assets/samples/`.
- **Human-in-the-loop** — optional, by design: Co-pilot step approval and Data Doctor fix approval.
- **Base** — built on the official KDD Cup 2026 starter kit; the Studio (UI, gateway, interaction
  controller, Data Doctor / Explore / Knowledge-Graph tooling) and the engine improvements
  (multi-agent, DRAGIN, Hybrid-B routing, self-correction, memory) are this team's contribution.

---

### Acknowledgements & contact

Built on the official **KDD Cup 2026 Data Agents** starter kit.

- Official website: https://dataagent.top
- Starter kit & issues: https://github.com/HKUSTDial/kddcup2026-data-agents-starter-kit
