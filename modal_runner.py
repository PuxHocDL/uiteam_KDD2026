import modal
import subprocess
import os
import sys


def _print_metrics():
    import subprocess as _sp, os as _os
    print()
    print("=" * 65)
    print("  CONTAINER HARDWARE METRICS")
    print("=" * 65)
    try:
        r = _sp.run(["nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,"
            "utilization.memory,temperature.gpu,power.draw,power.limit,driver_version",
            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            for i, line in enumerate(r.stdout.strip().split("\n")):
                p = [x.strip() for x in line.split(",")]
                if len(p) >= 10:
                    print(f"  GPU {i}: {p[0]}")
                    print(f"    Driver:       {p[9]}")
                    print(f"    VRAM:         {p[2]} / {p[1]} MiB  (free: {p[3]} MiB)")
                    print(f"    GPU Util:     {p[4]}%")
                    print(f"    Mem Util:     {p[5]}%")
                    print(f"    Temperature:  {p[6]} C")
                    print(f"    Power:        {p[7]} / {p[8]} W")
    except Exception as e:
        print(f"  [GPU] error: {e}")
    try:
        with open("/proc/cpuinfo") as _f:
            _ci = _f.read()
        _cores = _ci.count("processor\t:")
        _mn = ""
        for _ln in _ci.split("\n"):
            if _ln.startswith("model name"):
                _mn = _ln.split(":", 1)[1].strip()
                break
        print(f"  CPU: {_mn}  ({_cores} cores)")
    except:
        pass
    try:
        with open("/proc/meminfo") as _f:
            _mi = {}
            for _ln in _f:
                if ":" in _ln:
                    _k, _v = _ln.split(":", 1)
                    _mi[_k.strip()] = _v.strip()
        _t = int(_mi.get("MemTotal", "0 kB").split()[0])
        _a = int(_mi.get("MemAvailable", "0 kB").split()[0])
        _u = _t - _a
        print(f"  Memory: {_u / 1048576:.1f} / {_t / 1048576:.1f} GB  ({_u * 100 // _t}% used)")
    except:
        pass
    try:
        _st = _os.statvfs("/")
        _td = _st.f_blocks * _st.f_frsize
        _fd = _st.f_bavail * _st.f_frsize
        _ud = _td - _fd
        print(f"  Disk:   {_ud / (1024**3):.1f} / {_td / (1024**3):.1f} GB  (free: {_fd / (1024**3):.1f} GB)")
    except:
        pass
    print("=" * 65)
    print()

def _monitor_metrics(interval=30):
    import subprocess as _sp, time as _time, threading as _th
    def _loop():
        while True:
            _time.sleep(interval)
            try:
                r = _sp.run(["nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        p = [x.strip() for x in line.split(",")]
                        if len(p) >= 5:
                            print(f"[METRICS] GPU {p[0]}% | VRAM {p[1]}/{p[2]} MiB | {p[3]}C | {p[4]}W", flush=True)
            except:
                pass
            try:
                with open("/proc/loadavg") as _f:
                    _la = _f.read().split()[:3]
                print(f"[METRICS] CPU load {_la[0]} {_la[1]} {_la[2]}", flush=True)
            except:
                pass
    _th.Thread(target=_loop, daemon=True).start()


app = modal.App("m-gpux-llm-api")

MODEL_NAME = "Qwen/Qwen3.5-35B-A3B"
API_KEY = "sk-mgpux-8b37a191eb046c68b9805c1d381a61a2b33a7464c6c0bbe6"

vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("vllm", "transformers", "hf-transfer", "httpx", "fastapi", "uvicorn[standard]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

hf_cache = modal.Volume.from_name("m-gpux-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("m-gpux-vllm-cache", create_if_missing=True)

MINUTES = 60

PROXY_CODE = """
import httpx, json, os, asyncio, time as _time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response, JSONResponse
import uvicorn

API_KEY = os.environ["MGPUX_API_KEY"]
VLLM = "http://127.0.0.1:8001"

pool_limits = httpx.Limits(max_connections=300, max_keepalive_connections=150, keepalive_expiry=120)
timeout = httpx.Timeout(900.0, connect=15.0)
http_client = None

# ── In-flight request tracking for backpressure ──
import threading
_inflight = 0
_inflight_lock = threading.Lock()
MAX_INFLIGHT = 64  # reject new requests beyond this to avoid KV cache saturation

# ── Retry config ──
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = [1.0, 2.0, 4.0]  # seconds between retries

# ── Metrics tracking ──
_stats = {
    "start_time": _time.time(),
    "total_requests": 0,
    "total_success": 0,
    "total_errors_4xx": 0,
    "total_errors_5xx": 0,
    "total_retries": 0,
    "total_tokens_prompt": 0,
    "total_tokens_completion": 0,
    "latency_sum": 0.0,
    "latency_count": 0,
    "latency_min": float("inf"),
    "latency_max": 0.0,
    "latency_recent": [],       # last 50 latencies for p50/p95
    "peak_inflight": 0,
    "rejected_429": 0,
}
_stats_lock = threading.Lock()

def _record_request(latency, status_code, prompt_tokens=0, completion_tokens=0, retries=0):
    with _stats_lock:
        _stats["total_requests"] += 1
        if 200 <= status_code < 400:
            _stats["total_success"] += 1
        elif 400 <= status_code < 500:
            _stats["total_errors_4xx"] += 1
        else:
            _stats["total_errors_5xx"] += 1
        _stats["total_retries"] += retries
        _stats["total_tokens_prompt"] += prompt_tokens
        _stats["total_tokens_completion"] += completion_tokens
        if latency > 0:
            _stats["latency_sum"] += latency
            _stats["latency_count"] += 1
            if latency < _stats["latency_min"]:
                _stats["latency_min"] = latency
            if latency > _stats["latency_max"]:
                _stats["latency_max"] = latency
            _stats["latency_recent"].append(latency)
            if len(_stats["latency_recent"]) > 50:
                _stats["latency_recent"] = _stats["latency_recent"][-50:]
        if _inflight > _stats["peak_inflight"]:
            _stats["peak_inflight"] = _inflight

@asynccontextmanager
async def lifespan(app):
    global http_client
    http_client = httpx.AsyncClient(base_url=VLLM, limits=pool_limits, timeout=timeout)
    yield
    await http_client.aclose()

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def auth(request, call_next):
    path = request.url.path
    if path in ("/health", "/", "/docs", "/openapi.json", "/stats"):
        return await call_next(request)
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": {"message": "Missing API key. Use: Authorization: Bearer sk-mgpux-xxx", "type": "auth_error"}})
    if auth_header[7:] != API_KEY:
        return JSONResponse(status_code=403, content={"error": {"message": "Invalid API key", "type": "auth_error"}})
    return await call_next(request)

@app.get("/health")
async def health():
    try:
        r = await http_client.get("/v1/models", timeout=3)
        vllm_ready = r.status_code == 200
    except Exception:
        vllm_ready = False
    resp = {"status": "ok" if vllm_ready else "loading", "vllm_ready": vllm_ready, "inflight_requests": _inflight}
    return JSONResponse(content=resp, status_code=200)

@app.get("/stats")
async def stats():
    import subprocess as _sp
    with _stats_lock:
        s = dict(_stats)
        recent = sorted(s["latency_recent"]) if s["latency_recent"] else []
    uptime = _time.time() - s["start_time"]
    avg_latency = s["latency_sum"] / s["latency_count"] if s["latency_count"] else 0
    p50 = recent[len(recent)//2] if recent else 0
    p95 = recent[int(len(recent)*0.95)] if recent else 0
    p99 = recent[int(len(recent)*0.99)] if recent else 0
    rps = s["total_requests"] / uptime if uptime > 0 else 0
    # Try to get vLLM model info
    vllm_info = {}
    try:
        r = await http_client.get("/v1/models", timeout=3)
        if r.status_code == 200:
            vllm_info = r.json()
    except Exception:
        pass
    # ── GPU metrics via nvidia-smi ──
    gpus = []
    try:
        r = _sp.run(["nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,"
            "utilization.memory,temperature.gpu,power.draw,power.limit",
            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for i, line in enumerate(r.stdout.strip().splitlines()):
                p = [x.strip() for x in line.split(",")]
                if len(p) >= 9:
                    gpus.append({
                        "index": i,
                        "name": p[0],
                        "vram_total_mib": int(float(p[1])),
                        "vram_used_mib": int(float(p[2])),
                        "vram_free_mib": int(float(p[3])),
                        "gpu_util_pct": int(float(p[4])),
                        "mem_util_pct": int(float(p[5])),
                        "temperature_c": int(float(p[6])),
                        "power_draw_w": round(float(p[7]), 1),
                        "power_limit_w": round(float(p[8]), 1),
                    })
    except Exception:
        pass
    # ── CPU metrics ──
    cpu_info = {}
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            cpu_info["load_1m"] = float(parts[0])
            cpu_info["load_5m"] = float(parts[1])
            cpu_info["load_15m"] = float(parts[2])
    except Exception:
        pass
    try:
        with open("/proc/cpuinfo") as f:
            ci = f.read()
        cpu_info["cores"] = ci.count("processor" + chr(9) + ":")
        for ln in ci.splitlines():
            if ln.startswith("model name"):
                cpu_info["model"] = ln.split(":", 1)[1].strip()
                break
    except Exception:
        pass
    # ── RAM metrics ──
    ram_info = {}
    try:
        with open("/proc/meminfo") as f:
            mi = {}
            for ln in f:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    mi[k.strip()] = v.strip()
        total_kb = int(mi.get("MemTotal", "0 kB").split()[0])
        avail_kb = int(mi.get("MemAvailable", "0 kB").split()[0])
        ram_info["total_mb"] = round(total_kb / 1024)
        ram_info["used_mb"] = round((total_kb - avail_kb) / 1024)
        ram_info["available_mb"] = round(avail_kb / 1024)
        ram_info["used_pct"] = round((total_kb - avail_kb) / total_kb * 100, 1) if total_kb else 0
    except Exception:
        pass
    # ── Disk metrics ──
    disk_info = {}
    try:
        st = os.statvfs("/")
        total_b = st.f_blocks * st.f_frsize
        free_b = st.f_bavail * st.f_frsize
        used_b = total_b - free_b
        disk_info["total_gb"] = round(total_b / (1024**3), 1)
        disk_info["used_gb"] = round(used_b / (1024**3), 1)
        disk_info["free_gb"] = round(free_b / (1024**3), 1)
        disk_info["used_pct"] = round(used_b / total_b * 100, 1) if total_b else 0
    except Exception:
        pass
    return {
        "uptime_seconds": round(uptime, 1),
        "inflight_requests": _inflight,
        "peak_inflight": s["peak_inflight"],
        "max_inflight_limit": MAX_INFLIGHT,
        "total_requests": s["total_requests"],
        "total_success": s["total_success"],
        "total_errors_4xx": s["total_errors_4xx"],
        "total_errors_5xx": s["total_errors_5xx"],
        "total_retries": s["total_retries"],
        "rejected_429": s["rejected_429"],
        "requests_per_second": round(rps, 2),
        "latency_avg_ms": round(avg_latency * 1000, 1),
        "latency_min_ms": round(s["latency_min"] * 1000, 1) if s["latency_min"] != float("inf") else 0,
        "latency_max_ms": round(s["latency_max"] * 1000, 1),
        "latency_p50_ms": round(p50 * 1000, 1),
        "latency_p95_ms": round(p95 * 1000, 1),
        "latency_p99_ms": round(p99 * 1000, 1),
        "tokens_prompt_total": s["total_tokens_prompt"],
        "tokens_completion_total": s["total_tokens_completion"],
        "vllm_models": vllm_info,
        "gpus": gpus,
        "cpu": cpu_info,
        "ram": ram_info,
        "disk": disk_info,
    }

async def _proxy_with_retry(method, url, content, headers, is_stream):
    last_exc = None
    retries_used = 0
    for attempt in range(RETRY_ATTEMPTS):
        try:
            if is_stream:
                # Client wants streaming — pass through with error handling
                async def stream_response():
                    try:
                        async with http_client.stream(method, url, content=content, headers=headers) as r:
                            async for chunk in r.aiter_bytes():
                                yield chunk
                    except Exception as stream_exc:
                        # Yield a proper SSE error so client gets notified instead of
                        # Modal seeing a truncated response (TransferEncodingError)
                        err_payload = json.dumps({"error": {"message": f"Stream interrupted: {stream_exc}", "type": "server_error"}})
                        NL = chr(10)
                        yield f"data: {err_payload}{NL}{NL}data: [DONE]{NL}{NL}".encode()
                return StreamingResponse(stream_response(), media_type="text/event-stream"), 200, retries_used

            # ── Internal streaming for non-stream requests ──
            # Stream from vLLM internally to keep connection alive during long inference,
            # collect all chunks, then return as a normal response.
            collected = bytearray()
            status_code = 200
            content_type = "application/json"
            async with http_client.stream(method, url, content=content, headers=headers) as r:
                status_code = r.status_code
                content_type = r.headers.get("content-type", "application/json")
                async for chunk in r.aiter_bytes():
                    collected.extend(chunk)

            # Extract token usage from collected response
            prompt_tok = 0
            comp_tok = 0
            if content_type.startswith("application/json"):
                try:
                    rj = json.loads(collected)
                    usage = rj.get("usage", {})
                    prompt_tok = usage.get("prompt_tokens", 0)
                    comp_tok = usage.get("completion_tokens", 0)
                except Exception:
                    pass
            resp = Response(
                content=bytes(collected),
                status_code=status_code,
                media_type=content_type,
            )
            return resp, status_code, retries_used, prompt_tok, comp_tok
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError) as exc:
            last_exc = exc
            retries_used += 1
            if attempt < RETRY_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])
                continue
        except httpx.PoolTimeout as exc:
            resp = JSONResponse(status_code=429, content={"error": {"message": f"Server overloaded ({_inflight} requests in flight). Retry after a few seconds.", "type": "rate_limit_error"}, "retry_after": 5})
            return resp, 429, retries_used, 0, 0
        except Exception as exc:
            resp = JSONResponse(status_code=502, content={"error": {"message": f"Backend error: {exc}", "type": "server_error"}})
            return resp, 502, retries_used, 0, 0
    resp = JSONResponse(status_code=503, content={"error": {"message": f"vLLM not responding after {RETRY_ATTEMPTS} attempts. Model may still be loading.", "type": "server_error"}, "retry_after": 30})
    return resp, 503, retries_used, 0, 0

@app.api_route("/v1/{path:path}", methods=["GET","POST"])
async def proxy(request: Request, path: str):
    global _inflight
    # ── Backpressure: reject early if overloaded ──
    with _inflight_lock:
        if _inflight >= MAX_INFLIGHT:
            with _stats_lock:
                _stats["rejected_429"] += 1
            _record_request(0, 429)
            return JSONResponse(status_code=429, content={"error": {"message": f"Too many concurrent requests ({_inflight}/{MAX_INFLIGHT}). Please retry.", "type": "rate_limit_error"}, "retry_after": 5})
        _inflight += 1
    t0 = _time.time()
    try:
        body = await request.body()
        headers = {k:v for k,v in request.headers.items() if k.lower() not in ("host","authorization","content-length")}
        is_stream = False
        if body:
            try: is_stream = json.loads(body).get("stream", False)
            except: pass
        result = await _proxy_with_retry(request.method, f"/v1/{path}", body, headers, is_stream)
        latency = _time.time() - t0
        if is_stream:
            resp, status, retries = result[0], result[1], result[2]
            _record_request(latency, status, retries=retries)
            return resp
        resp, status, retries, ptok, ctok = result
        _record_request(latency, status, prompt_tokens=ptok, completion_tokens=ctok, retries=retries)
        return resp
    finally:
        with _inflight_lock:
            _inflight -= 1

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info",
                backlog=2048, limit_concurrency=300, limit_max_requests=None,
                timeout_keep_alive=120)
"""


@app.function(
    image=vllm_image,
    gpu="RTX-PRO-6000",
    timeout=24 * 60 * MINUTES,
    scaledown_window=5 * MINUTES,
    min_containers=1,
    volumes={
        "/root/.cache/huggingface": hf_cache,
        "/root/.cache/vllm": vllm_cache,
    },
)
@modal.concurrent(max_inputs=64)
@modal.web_server(port=8000, startup_timeout=20 * MINUTES)
def serve():
    _print_metrics()
    _monitor_metrics()

    # Start vLLM on port 8001 (background)
    vllm_cmd = [
        "vllm", "serve", MODEL_NAME,
        "--served-model-name", MODEL_NAME,
        "--host", "0.0.0.0",
        "--port", "8001",
        "--tensor-parallel-size", "1",
        "--seed", "1024",
        "--max-model-len", "262144",
        "--gpu-memory-utilization", "0.96",
        "--enable-prefix-caching",
        "--max-num-seqs", "96",
        "--enable-chunked-prefill",
        "--max-num-batched-tokens", "10000",
        "--trust-remote-code",
    ]
    vllm_cmd += ["--reasoning-parser", "qwen3", "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder"]
    import threading, time as _time

    print("[M-GPUX] Starting vLLM on :8001:", " ".join(vllm_cmd))

    def _watch_vllm():
        backoff = 2
        while True:
            proc = subprocess.Popen(vllm_cmd)
            print(f"[M-GPUX] vLLM started (pid={proc.pid})")
            proc.wait()
            code = proc.returncode
            if code == 0:
                print("[M-GPUX] vLLM exited cleanly (code 0), not restarting")
                break
            print(f"[M-GPUX] vLLM crashed (exit code {code}), restarting in {backoff}s...")
            _time.sleep(backoff)
            backoff = min(backoff * 2, 60)
    threading.Thread(target=_watch_vllm, daemon=True).start()

    # Start auth proxy on port 8000 (Modal detects this immediately)
    os.environ["MGPUX_API_KEY"] = API_KEY
    with open("/tmp/_proxy.py", "w") as f:
        f.write(PROXY_CODE)
    print("[M-GPUX] Starting auth proxy on :8000")

    def _watch_proxy():
        while True:
            proc = subprocess.Popen([sys.executable, "/tmp/_proxy.py"])
            print(f"[M-GPUX] Proxy started (pid={proc.pid})")
            proc.wait()
            print(f"[M-GPUX] Proxy exited with code {proc.returncode}, restarting in 1s...")
            _time.sleep(1)
    threading.Thread(target=_watch_proxy, daemon=True).start()
