"""
Automation Worker —— 独立进程，负责真正"执行"队列里的任务。

跟 API（automation/app/main.py）共用同一个 Docker 镜像，只是启动命令不一样：
    API 启动命令:    uvicorn automation.app.main:app --host 0.0.0.0 --port 8000
    Worker 启动命令: python -m automation.app.worker

设计要点：
- 幂等：入队阶段的幂等由 RedisAutomationStore.enqueue() 保证。
  Worker 这边额外保证：拿到任务后先检查状态是不是还是 queued，
  不是的话说明已经被别的 Worker 处理过，直接跳过。
- 租约：Worker 领取任务时设置 lease_seconds 秒的租约；如果这段时间内
  Worker 崩溃/被杀，没人 renew 或 complete 这个租约，
  reap_expired_leases() 会把任务重新放回队列，交给别的 Worker 重试。
- 超时：每个任务执行有硬性超时 JOB_TIMEOUT_SECONDS，超时按失败处理，走重试逻辑。
- 重试：任务失败后，没到最大重试次数就重新入队；用完次数才标成最终 failed。
- 并发：单个 Worker 进程内用线程池同时处理 WORKER_CONCURRENCY 个任务
  （任务本质是"调用网关、等 HTTP 返回"，属于 IO 密集型，线程池够用，不需要上多进程）。
- 优雅停止：监听 SIGTERM，收到信号后不再领取新任务，
  等当前正在执行的任务在宽限期内做完（或租约自然过期交给别的 Worker）再退出。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import redis
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from automation.app.redis_store import RedisAutomationStore

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":%(message)s}')
log = logging.getLogger("polygate.automation.worker")

REDIS_URL = os.environ.get("AUTOMATION_REDIS_URL", "redis://redis:6379/0")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8000")
# opt-in：留空就和 Gateway 那边的 POLYGATE_API_KEYS 逻辑一致，裸调不带认证；
# 部署时要开启 Gateway 那层校验的话，这里也要配上同一个 key。
# 环境变量名暂定，需要和 D/C 一起最终确认（A 建议的方式）。
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")

POLL_INTERVAL_SECONDS = float(os.environ.get("WORKER_POLL_INTERVAL_SECONDS", "1.0"))
LEASE_SECONDS = int(os.environ.get("WORKER_LEASE_SECONDS", "60"))
JOB_TIMEOUT_SECONDS = float(os.environ.get("WORKER_JOB_TIMEOUT_SECONDS", "30"))
MAX_RETRIES = int(os.environ.get("WORKER_MAX_RETRIES", "3"))
GRACEFUL_SHUTDOWN_SECONDS = float(os.environ.get("WORKER_SHUTDOWN_GRACE_SECONDS", "45"))
CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "5"))
REAP_INTERVAL_SECONDS = float(os.environ.get("WORKER_REAP_INTERVAL_SECONDS", "10"))

_shutdown_requested = threading.Event()
_last_heartbeat = time.time()
_retry_counts: dict[str, int] = {}

# 标准 Prometheus 指标（和 Gateway 那边用同一个库、同一种风格），
# 而不是之前手写的纯文本行——这样 Prometheus/Grafana 抓取和展示方式完全一致。
JOBS_PROCESSED_TOTAL = Counter(
    "automation_worker_jobs_processed_total", "已成功完成的任务数"
)
JOBS_FAILED_TOTAL = Counter(
    "automation_worker_jobs_failed_total", "最终失败（重试用完）的任务数"
)
JOBS_RETRIED_TOTAL = Counter(
    "automation_worker_jobs_retried_total", "触发过重试的次数（可能同一个任务多次计入）"
)
JOB_DURATION_SECONDS = Histogram(
    "automation_worker_job_duration_seconds", "单次任务执行耗时（从 claim 到 完成/失败）"
)


def _handle_signal(signum, frame):
    log.info(f'{{"event":"shutdown_signal_received","signal":{signum}}}')
    _shutdown_requested.set()


def execute_job(store: RedisAutomationStore, job) -> None:
    """真正执行一个任务：读取入队时存好的内部执行快照（完整 gateway_request），
    调用 PolyGate 网关，把结果写回去。

    采用 C 的方案：完整请求内容（含用户 prompt）从不放进公开的 JobRecord，
    只存在 Redis 里一个 Worker 专用的 key（job-payload）里，这样：
    - 不依赖会过期的 PreviewResponse，任务排多久队都不受影响
    - GET /v1/jobs、GET /v1/jobs/{id} 这些公开接口不会泄露 prompt 内容
    - 不需要改动公开的 JobRecord 结构，D 那边不用跟着改解析代码

    认证：Gateway 那边的 POLYGATE_API_KEYS 是 opt-in 的（部署时留空就不校验）。
    这里同样做成 opt-in——GATEWAY_API_KEY 有值才带 Authorization 头，
    没配就跟以前一样裸调，两边行为自动对齐，不需要本地/演示环境额外配置。
    """
    global _last_heartbeat
    job_id = job.job_id
    started = time.perf_counter()
    try:
        payload = store.get_execution_payload(job_id)
        if payload is None:
            raise RuntimeError("execution payload missing or expired（任务对应的内部快照找不到了）")

        headers = {"Authorization": f"Bearer {GATEWAY_API_KEY}"} if GATEWAY_API_KEY else {}
        with httpx.Client(timeout=JOB_TIMEOUT_SECONDS) as client:
            resp = client.post(f"{GATEWAY_URL}/v1/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()
        store.complete_job(job_id, result)
        JOBS_PROCESSED_TOTAL.inc()
        log.info(f'{{"event":"job_completed","job_id":"{job_id}"}}')
    except Exception as exc:
        retries = _retry_counts.get(job_id, 0)
        if retries < MAX_RETRIES:
            _retry_counts[job_id] = retries + 1
            JOBS_RETRIED_TOTAL.inc()
            log.warning(f'{{"event":"job_retry","job_id":"{job_id}","attempt":{retries + 1},"err":"{exc}"}}')
            store.requeue_job(job_id)
        else:
            JOBS_FAILED_TOTAL.inc()
            log.error(f'{{"event":"job_failed","job_id":"{job_id}","err":"{exc}"}}')
            store.fail_job(job_id, str(exc))
    finally:
        JOB_DURATION_SECONDS.observe(time.perf_counter() - started)
        _last_heartbeat = time.time()


def worker_loop(store: RedisAutomationStore) -> None:
    log.info('{"event":"worker_started"}')
    last_reap = 0.0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        in_flight = []
        while not _shutdown_requested.is_set():
            global _last_heartbeat
            _last_heartbeat = time.time()

            now = time.time()
            if now - last_reap >= REAP_INTERVAL_SECONDS:
                reaped = store.reap_expired_leases()
                if reaped:
                    log.warning(f'{{"event":"leases_reaped","job_ids":{reaped}}}')
                last_reap = now

            in_flight = [f for f in in_flight if not f.done()]
            if len(in_flight) >= CONCURRENCY:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            job = store.claim_next_job(lease_seconds=LEASE_SECONDS)
            if job is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            log.info(f'{{"event":"job_claimed","job_id":"{job.job_id}"}}')
            in_flight.append(pool.submit(execute_job, store, job))

        # 优雅停止：不再领取新任务，等待正在执行的任务在宽限期内做完
        deadline = time.time() + GRACEFUL_SHUTDOWN_SECONDS
        for f in in_flight:
            remaining = max(0.0, deadline - time.time())
            try:
                f.result(timeout=remaining)
            except Exception:
                pass  # 超过宽限期还没做完的，租约会自然过期，交给别的 Worker 重新领取

    log.info('{"event":"worker_stopping_gracefully"}')


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            healthy = (time.time() - _last_heartbeat) < max(POLL_INTERVAL_SECONDS * 5, 10)
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok" if healthy else "stalled"}).encode())
        elif self.path == "/metrics":
            body = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # 不用默认的访问日志，避免刷屏


def _run_health_server():
    port = int(os.environ.get("WORKER_HEALTH_PORT", "9000"))
    HTTPServer(("0.0.0.0", port), _HealthHandler).serve_forever()


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    redis_client = redis.Redis.from_url(REDIS_URL)
    store = RedisAutomationStore(redis_client)

    threading.Thread(target=_run_health_server, daemon=True).start()
    worker_loop(store)


if __name__ == "__main__":
    main()