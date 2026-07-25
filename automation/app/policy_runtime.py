"""Task 4: Worker 侧的策略运行时客户端。

镜像 A 的 gateway/app/policy.py（GatewayPolicyRuntime），行为约定保持一致：
持有一个不可变快照，用 If-None-Match 条件 GET 轮询 Policy API，任何失败都
保留 Last Known Good 且不向外抛异常。

与 Gateway 的两处有意差异：

1. Worker 在 automation 包内，所以直接复用契约模型 ActivePolicyResponse /
   PolicyStoreDocument，不像 Gateway 那样手工走 body["policy"]["gateway"]。
2. 启动时如果挂载文件和 Policy API 都拿不到策略，抛 PolicyLoadError 让进程
   退出，而不是像 Gateway 那样降级到硬编码默认值。策略是调度正确性的来源，
   静默用默认值等于隐藏配置错误；K8s 会重启 Pod 并在事件里暴露问题。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx
from prometheus_client import Counter, Gauge
from pydantic import ValidationError

from automation.app.policy_models import (
    ActivePolicyResponse,
    PolicyStoreDocument,
    QueuePolicy,
)

log = logging.getLogger("automation.policy_runtime")

# contracts/README.md 冻结了这个字符串（component="gateway" 或 "automation-worker"），
# Grafana 按值分别查询，不得改动。
COMPONENT = "automation-worker"

ACTIVE_POLICY_PATH = "/v1/policies/active"
DEFAULT_STORE_PATH = "/config/policy-store.json"
DEFAULT_API_BASE_URL = "http://automation:8020"
DEFAULT_REFRESH_SECONDS = 5

# 这两个指标刻意定义在本模块，而不是 automation/app/policy_metrics.py。
# policy_metrics 里的 ACTIVE_VERSION 是无标签 Gauge，一旦被 import 就会立刻以 0
# 出现在本进程的 /metrics 上；Worker 若 import 它，:9000 会冒出一条假的
# polygate_policy_active_version 0，与 Policy API 的真实值形成两条 series，
# 直接破坏 contracts/README.md 里 "active 与 loaded 版本差异 >30 秒告警" 的判据。
POLICY_LOADED_VERSION = Gauge(
    "polygate_policy_loaded_version",
    "Policy version loaded by this component.",
    ["component"],
)
POLICY_RELOAD_FAILURES = Counter(
    "polygate_policy_reload_failures_total",
    "Policy reload failures.",
    ["component", "reason"],
)


def record_policy_loaded_version(version: int) -> None:
    POLICY_LOADED_VERSION.labels(component=COMPONENT).set(version)


def record_policy_reload_failure(reason: str) -> None:
    """reason 取值被 contracts/README.md 冻结为 network|http|validation|file。"""
    POLICY_RELOAD_FAILURES.labels(component=COMPONENT, reason=reason).inc()


class PolicyLoadError(RuntimeError):
    """启动时既没有可用的挂载文件、也拉不到 Policy API——没有 LKG 可退守。"""


def _resolve_active_policy_url(policy_url: str) -> str:
    """POLICY_API_URL 给的是裸 origin，但调用方也可以直接传完整 URL。"""
    parsed = urlparse(policy_url)
    if parsed.path in ("", "/"):
        return f"{policy_url.rstrip('/')}{ACTIVE_POLICY_PATH}"
    return policy_url


def _snapshot_from_store_version(document: PolicyStoreDocument) -> ActivePolicyResponse:
    """把挂载的 ConfigMap 文档转成与 API 响应同构的快照。

    组装方式与 Policy API 自己的做法一致（automation/app/main.py 的
    active_policy 路由），保证文件 bootstrap 和 HTTP 刷新产出同一种对象。
    """
    active = document.active
    return ActivePolicyResponse(
        version=active.version,
        schema_version=active.policy.schema_version,
        published_at=active.created_at,
        policy=active.policy,
    )


class PolicyRuntime:
    """持有一个 ActivePolicyResponse 快照，由后台线程按固定间隔刷新。"""

    def __init__(
        self,
        mounted_file: str | Path | None = None,
        policy_url: str | None = None,
        refresh_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._mounted_file = Path(
            mounted_file or os.environ.get("POLICY_FILE", DEFAULT_STORE_PATH)
        )
        self._policy_url = _resolve_active_policy_url(
            policy_url or os.environ.get("POLICY_API_URL", DEFAULT_API_BASE_URL)
        )
        self.refresh_seconds = (
            refresh_seconds
            if refresh_seconds is not None
            else float(os.environ.get("POLICY_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS))
        )
        self._lock = threading.Lock()
        self._client = httpx.Client(transport=transport, timeout=5.0)
        self._thread: threading.Thread | None = None
        self._active = self._load_initial_snapshot()

    # ---------- 启动加载 ----------

    def _load_initial_snapshot(self) -> ActivePolicyResponse:
        """挂载文件优先，失败则回退到一次 HTTP 拉取，都不行就抛异常。"""
        try:
            raw = self._mounted_file.read_text(encoding="utf-8")
            document = PolicyStoreDocument.model_validate_json(raw)
        except FileNotFoundError:
            log.warning('{"event":"policy_store_not_mounted","fallback":"http"}')
            record_policy_reload_failure("file")
            return self._load_initial_over_http("mounted policy store is absent")
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            log.warning(f'{{"event":"policy_store_unreadable","err":"{exc}"}}')
            record_policy_reload_failure("file")
            return self._load_initial_over_http("mounted policy store is unreadable")

        snapshot = _snapshot_from_store_version(document)
        record_policy_loaded_version(snapshot.version)
        log.info(f'{{"event":"policy_bootstrapped_from_file","version":{snapshot.version}}}')
        return snapshot

    def _load_initial_over_http(self, file_problem: str) -> ActivePolicyResponse:
        try:
            response = self._client.get(self._policy_url)
            response.raise_for_status()
            snapshot = ActivePolicyResponse.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            # 这是唯一允许抛异常的路径：此刻还没有任何 LKG 可以退守。
            raise PolicyLoadError(
                f"{file_problem} and the Policy API is unavailable: {exc}"
            ) from None

        record_policy_loaded_version(snapshot.version)
        log.info(f'{{"event":"policy_bootstrapped_from_api","version":{snapshot.version}}}')
        return snapshot

    # ---------- 读取 ----------

    def snapshot(self) -> ActivePolicyResponse:
        """返回当前快照。调用方必须视其为只读——这里不做深拷贝，换版本时只替换指针。"""
        with self._lock:
            return self._active

    def queue_policy(self) -> QueuePolicy:
        """Worker 热路径唯一需要的东西。"""
        return self.snapshot().policy.automation.queue

    # ---------- 刷新 ----------

    def refresh_once(self) -> bool:
        """轮询一次 Policy API。返回是否换上了新快照。任何失败都保留 LKG 且不抛异常。"""
        # ETag 由版本号推导（服务端就是这个格式），这样进程重启后第一次轮询
        # 就已经是条件请求，不会白拉一次全量 body。
        headers = {"If-None-Match": f'"policy-v{self._active.version}"'}
        try:
            response = self._client.get(self._policy_url, headers=headers)
        except httpx.TransportError as exc:
            # TransportError 已覆盖 TimeoutException / ConnectError。
            log.warning(f'{{"event":"policy_refresh_transport_error","err":"{exc}"}}')
            record_policy_reload_failure("network")
            return False

        if response.status_code == 304:
            return False

        if response.status_code != 200:
            log.warning(
                f'{{"event":"policy_refresh_unexpected_status","status":{response.status_code}}}'
            )
            record_policy_reload_failure("http")
            return False

        try:
            candidate = ActivePolicyResponse.model_validate(response.json())
        except (KeyError, ValidationError, ValueError) as exc:
            log.warning(f'{{"event":"policy_refresh_invalid_body","err":"{exc}"}}')
            record_policy_reload_failure("validation")
            return False

        with self._lock:
            self._active = candidate
        record_policy_loaded_version(candidate.version)
        log.info(f'{{"event":"policy_reloaded","version":{candidate.version}}}')
        return True

    # ---------- 后台线程 ----------

    def start(self, stop_event: threading.Event) -> threading.Thread:
        """起一个 daemon 线程按 refresh_seconds 轮询，由 stop_event 终止。

        Worker 直接把自己的 _shutdown_requested 传进来，所以 SIGTERM 会立刻
        打断 wait()，不必睡满一个刷新间隔。
        """
        thread = threading.Thread(
            target=self._loop,
            args=(stop_event,),
            name="policy-refresh",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return thread

    def _loop(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.refresh_seconds):
            try:
                self.refresh_once()
            except Exception:
                # refresh_once 自己已经吞掉了预期内的失败；这里兜住意外异常，
                # 保证刷新线程不会因为一次异常就永久死掉。
                log.exception('{"event":"policy_refresh_unexpected_error"}')
        log.info('{"event":"policy_refresh_stopped"}')

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def close(self) -> None:
        self._client.close()
