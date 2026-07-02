# Data Agent Studio — Reproduction Guide (Creative Track)

> Before submission, copy/rename this file to `creative_<team_id>_guide.md`
> (or export to PDF) per the KDD Cup 2026 Creative Track submission rules —
> see `docs/KDD_Cup_2026_Creative_Track.md`, section 9.2.

This guide covers building and running the **Docker image artifact**
(`creative_<team_id>_code.zip` / `creative_<team_id>:v1`) submitted for the
Creative Track. One image, two purposes:

1. **Interactive Studio** (default `docker run`) — the FastAPI gateway plus
   the React/Vite chat UI, for the live demo.
2. **Verification surface** (pass a command to `docker run`) — the same
   engine's `dabench` benchmark/eval CLI and the pytest suite, so a judge (or
   you) can reproduce quantitative results and re-run tests against the exact
   code in the image, per the Reproducibility statement in the report
   (section 8) and rule 2 in `docs/KDD_Cup_2026_Creative_Track.md` §8
   ("code behavior must match the demo").

There is no separate headless benchmark image — the old `image code/Dockerfile`
has been retired in favor of this single image with mode-switching entrypoint.

> **Verified** (2026-07-02, Docker 29.6.1, `docker build -t creative_test:v1 .`
> from a clean tree): build succeeded (964MB content size); `docker run
> creative_test:v1 pytest -q` → **133 passed** in 6.23s; `docker run
> creative_test:v1 dabench --help` listed all 7 subcommands; Studio mode
> (`docker run -p 8000:8000 -p 5173:5173 ...`) → `/api/health` returned
> `{"ok":true,"tasks":0}` and `http://localhost:5173/` returned HTTP 200
> within ~12s of start.

## 1. What's in the image

| Component | Source | Served on / invoked as |
|---|---|---|
| FastAPI gateway (`server/app.py`) | `src/`, `server/`, `assets/` | `http://localhost:8000` (default run) |
| React/Vite chat UI (built) | `frontend/` (built with `npm run build`) | `http://localhost:5173` (default run) |
| `dabench` benchmark/eval CLI | same `src/` | `docker run <image> dabench ...` |
| pytest suite (133 tests) | `tests/` | `docker run <image> pytest -q` |
| Analysis scripts | `scripts/` | `docker run <image> python scripts/analyze_reliability.py ...` |
| Example configs (no secrets) | `configs/*.example.yaml` | copy to `configs/*.yaml` at runtime |
| ruff (linter) | dev extra | `docker run <image> ruff check .` |

All processes run inside one container; `docker-entrypoint.sh` starts the
Studio (backend + UI) when invoked with no arguments, or `exec`s whatever
command you pass otherwise (`pytest`, `dabench ...`, etc.). No API key or
credential is baked into the image — the UI's Settings panel lets you enter
your own LLM endpoint/key at runtime (forwarded per-request, see
`server/app.py`), and the CLI path takes credentials via `--api-key`/
`--api-base`/`--model` flags or `-e AZURE_OPENAI_API_KEY=...` / `-e
DABENCH_API_KEY=...` env vars (see `src/data_agent_baseline/config.py`,
`CredentialOverrides`).

### 1.1 `dabench` subcommands (all runnable as `docker run <image> dabench <cmd> ...`)

| Command | Purpose | Key options |
|---|---|---|
| `status` | Show project layout / public dataset presence | `--config` |
| `inspect-task` | Show one task's metadata + available context files | `task_id`, `--config` |
| `run-task` | Run the agent on a single task | `task_id`, `--config`, `--model/--api-base/--api-key/--api-version` |
| `run-benchmark` | Run the agent across the configured task selection | `--config`, `--limit`, `--official-only`, + credential flags |
| `run-consensus` | Run the benchmark multiple times, merge best-of candidates | `--config`, `--max-rounds` (3–10), `--use-selector`, `--official-only`, + credential flags |
| `eval` | Score predictions vs. gold, print per-task/per-difficulty tables incl. tokens/cost | `--run-dir`, `--gold-dir`, `--lambda`, `--official-only` |
| `eval-consensus` | Same as `eval` but for a consensus run (all rounds + final) | `--run-dir`, `--gold-dir`, `--lambda`, `--official-only` |

Credential precedence for every command that calls the model: **CLI flag >
process env (`DABENCH_*` or `AZURE_OPENAI_*`) > YAML config** — so a
`docker run -e AZURE_OPENAI_API_KEY=...` works without editing any file.

## 2. Prerequisites

- Docker Engine or Docker Desktop (tested with Docker 28.5.1). No GPU
  required — the container only runs the agent orchestration and tool
  layer; LLM inference happens on whatever OpenAI-compatible / Azure
  endpoint you configure in the UI.
- An OpenAI-compatible (or Azure OpenAI) API key for the model you want to
  drive the agent with. Any endpoint compatible with the OpenAI Chat
  Completions API works.
- Ports `8000` and `5173` free on the host (or remap them, see below).

## 3. Option A — load the prebuilt image

If you received `creative_<team_id>_code.tar.gz` as a Docker export:

```bash
docker load -i creative_<team_id>_code.tar.gz
docker run --rm -p 8000:8000 -p 5173:5173 creative_<team_id>:v1
```

## 4. Option B — build from the code archive

If you received `creative_<team_id>_code.zip` (source archive) instead:

```bash
unzip creative_<team_id>_code.zip -d creative_<team_id>
cd creative_<team_id>

docker build -t creative_<team_id>:v1 .
docker run --rm -p 8000:8000 -p 5173:5173 creative_<team_id>:v1
```

Build takes a few minutes on a cold cache (frontend `npm ci` + `npm run
build`, then `uv sync --frozen --extra dev` for the Python deps — the `dev`
extra pulls in `pytest`/`ruff` too, since this image doubles as the
verification surface); faster on a warm Docker layer cache.

## 5. Using the app

1. Open **http://localhost:5173** in a browser.
2. Open **Settings** in the UI and enter your model endpoint, API key, and
   model name (OpenAI-compatible or Azure). Nothing is sent anywhere except
   your own backend at `http://localhost:8000`, which relays it to the
   endpoint you configured.
3. Pick a sample dataset (bundled under `assets/samples/`, e.g.
   `sales_2024.csv`, `crm.db`, `shop.db`) or upload your own CSV/JSON/SQLite
   files, then ask a natural-language question.
4. Choose **Autopilot** to watch the agent run end-to-end, or **Co-pilot**
   to approve/edit/reject each proposed tool call before it executes.

### Health check

```bash
curl http://localhost:8000/api/health
# -> {"ok":true,"tasks":0}
```

The container also declares a Docker `HEALTHCHECK` against this endpoint,
so `docker ps` will show `(healthy)` once the backend is fully up
(~10–15s after start).

## 6. Remapping ports

If `8000`/`5173` are taken on your host:

```bash
docker run --rm -p 18000:8000 -p 15173:5173 creative_<team_id>:v1
```

Then open `http://localhost:15173` and, in the UI Settings, set the API
base URL to `http://localhost:18000` (the UI defaults to
`http://localhost:8000`, stored in `localStorage`, and is editable there).

## 7. Expected results

- `docker build` completes without errors and produces an image a few
  hundred MB in size (Python data/ML deps dominate; the frontend bundle is
  a few hundred KB).
- `docker run` starts both processes; within ~15s `/api/health` returns
  `{"ok": true, ...}` and `http://localhost:5173/` returns HTTP 200 with
  the chat UI.
- Running a sample task (e.g. "What were total sales by region in 2024?"
  against `sales_2024.csv`) in Autopilot mode produces a live trace
  (thought → action → observation, looped) ending in a final answer table,
  streamed over SSE and rendered in the UI as it happens.
- In Co-pilot mode, the run pauses before each proposed tool call
  (`AWAITING_USER`) until you Approve / Edit / Reject / Guide it.

## 8. Stopping / cleanup

```bash
docker ps                      # find the container ID/name
docker stop <container>        # graceful shutdown (SIGTERM, ~5s grace)
docker rmi creative_<team_id>:v1
```

## 9. Data & state

- Bundled sample datasets: `assets/samples/` (copied into the image).
- Uploaded files and chat sessions are written to `/app/artifacts/` inside
  the container. This is **not** persisted across `docker run` invocations
  unless you mount a volume, e.g.:
  ```bash
  docker run --rm -p 8000:8000 -p 5173:5173 \
    -v "$(pwd)/artifacts:/app/artifacts" \
    creative_<team_id>:v1
  ```
- Ground-truth/answer files for any benchmark task are never exposed to the
  agent's tools — this is enforced at the tool layer (`PermissionError`),
  not just by the UI.

## 10. Verification: re-running the test suite and the benchmark

This is the part a judge (or you, before submitting) uses to confirm the
container's behavior matches the report/video and isn't hard-coded.

### 10.1 Test suite (no external data or API key needed)

```bash
docker run --rm creative_<team_id>:v1 pytest -q
```

The 11 modules under `tests/` are self-contained (in-memory + `tmp_path`
fixtures) — this alone verifies the agent loop, tool registry, scoring,
credential resolution, and server routes without touching a real LLM.

### 10.2 Benchmark / eval (needs the DataAgent-Bench dataset + your API key)

The dataset (`data/public/`, ~1.8GB) is intentionally **not** baked into the
image — mount it read-only, along with a config copied from a template:

```bash
cp configs/hybrid_b_baseline.example.yaml configs/hybrid_b.yaml   # add nothing secret here — pass creds via env instead

docker run --rm \
  -v "$(pwd)/data/public:/app/data/public:ro" \
  -v "$(pwd)/configs:/app/configs:ro" \
  -v "$(pwd)/artifacts:/app/artifacts" \
  -e AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" \
  -e AZURE_OPENAI_ENDPOINT="$AZURE_OPENAI_ENDPOINT" \
  creative_<team_id>:v1 dabench run-benchmark --config /app/configs/hybrid_b.yaml
```

`AZURE_OPENAI_*` / `DABENCH_*` env vars and `--model`/`--api-base`/`--api-key`/
`--api-version` flags are resolved with precedence CLI flag > env var > YAML
— so a config file with no secret in it still works (see
`src/data_agent_baseline/config.py::CredentialOverrides`).

Then score and inspect cost/token usage:

```bash
docker run --rm -v "$(pwd)/artifacts:/app/artifacts" creative_<team_id>:v1 dabench eval
```

`dabench eval`'s table includes per-task and per-difficulty token counts and
estimated USD cost (from `agents/model.py`'s usage tracking), which is the
quantitative evidence for report section 6 (cost/latency) and section 5
(evaluation).

### 10.3 Analysis scripts

```bash
docker run --rm -v "$(pwd)/artifacts:/app/artifacts" creative_<team_id>:v1 \
  python scripts/analyze_reliability.py --help
docker run --rm -v "$(pwd)/artifacts:/app/artifacts" creative_<team_id>:v1 \
  python scripts/compare_cost_quality.py --help
```

## 11. Beyond the demo: can this image host the app for real?

Yes — it's a standard Docker image, deployable to any Docker host (VPS,
ECS/Cloud Run, a k8s Deployment, `docker compose`, ...), not just a local
demo container. What's already verified working (see the "Verified" note near the top, and
§7): backend + frontend start, healthcheck passes, both ports serve real
traffic.

Before pointing real users at it (as opposed to a judge/demo running it
locally for a few minutes), address these gaps — none block the Creative
Track submission, but they matter for an actual deployment:

| Gap | Current state | Fix before real hosting |
|---|---|---|
| CORS | `server/app.py`: `allow_origins=["*"]` (wide open) | Restrict to your real frontend origin(s) |
| TLS/HTTPS | None inside the container | Put nginx/Caddy/Traefik in front, terminate TLS there |
| Frontend serving | `python -m http.server` (dev-grade: no gzip, no cache headers, weak concurrency) | Serve `frontend_dist/` via nginx or a CDN instead |
| State (`/app/artifacts`: sessions, auth, run history) | In-container only, lost on restart unless volume-mounted | Always mount `-v artifacts:/app/artifacts` (or a real volume/DB) in production |
| LLM credentials | Per-user, entered in the UI Settings panel each session (nothing baked in, nothing server-side shared) | Fine as-is for BYO-key use; if you want one shared backend key for many users, that's a new feature, not present today |
| Scaling | One container runs both API and static UI | Production setups typically split these into two services so they scale independently |

## 12. Known limitations

- The image bundles no LLM weights — you must supply an API-compatible
  endpoint. There is no offline/local-model mode in this build.
- The two processes (API + static UI) share one container for submission
  simplicity; a production deployment would typically split them into
  separate services behind a reverse proxy / TLS terminator (see §11).
