"""Task 4 Step 1: Worker 侧 PolicyRuntime 的客户端测试。

这个文件刻意不 import redis_store，所以没有 Redis 也能跑。
所有计数器断言都用前后增量，因为 prometheus 默认 registry 在整个
pytest session 内是进程全局的（既有先例：test_policy_api.py）。
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import httpx
import pytest

from automation.app.policy_models import QueuePolicy
from automation.app.policy_runtime import (
    COMPONENT,
    POLICY_LOADED_VERSION,
    POLICY_RELOAD_FAILURES,
    PolicyLoadError,
    PolicyRuntime,
)

EXAMPLES_PATH = Path(__file__).resolve().parents[2] / "contracts" / "policy-examples.json"
EXAMPLES = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))

# contracts/policy-examples.json 的 queue 块就是 v1 基线，与改造前的硬编码常量一致。
V1_QUEUE = QueuePolicy.model_validate(EXAMPLES["draft"]["automation"]["queue"])


def _store_file(tmp_path: Path, version: int, **queue_overrides) -> Path:
    """写一份只含单个 active 版本的挂载文件。

    PolicyStoreDocument._integrity 要求 active_version == max(versions) 且恰好
    一条 status="active"，所以这里只保留一条记录并改写版本号。
    """
    document = copy.deepcopy(EXAMPLES["store"])
    record = copy.deepcopy(document["versions"][0])
    record["version"] = version
    record["status"] = "active"
    record["rollback_from"] = None
    record["policy"]["automation"]["queue"].update(queue_overrides)
    document["active_version"] = version
    document["versions"] = [record]

    path = tmp_path / "policy-store.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _active_body(version: int, **queue_overrides) -> dict:
    body = copy.deepcopy(EXAMPLES["active_response"])
    body["version"] = version
    body["policy"]["automation"]["queue"].update(queue_overrides)
    return body


def _runtime(tmp_path, handler=None, *, version=1, mounted=True, **kwargs) -> PolicyRuntime:
    mounted_file = _store_file(tmp_path, version) if mounted else tmp_path / "absent.json"
    transport = httpx.MockTransport(handler) if handler is not None else None
    return PolicyRuntime(
        mounted_file=mounted_file,
        policy_url="http://automation:8020",
        transport=transport,
        **kwargs,
    )


def _failure_count(reason: str) -> float:
    return POLICY_RELOAD_FAILURES.labels(component=COMPONENT, reason=reason)._value.get()


def _loaded_version() -> float:
    return POLICY_LOADED_VERSION.labels(component=COMPONENT)._value.get()


# ---------- bootstrap ----------


def test_mounted_store_bootstraps_active_version(tmp_path):
    runtime = _runtime(tmp_path, version=1)

    assert runtime.snapshot().version == 1
    assert runtime.queue_policy() == V1_QUEUE
    assert _loaded_version() == 1


def test_missing_file_falls_back_to_http(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_active_body(9))

    runtime = _runtime(tmp_path, handler, mounted=False)

    assert runtime.snapshot().version == 9


def test_missing_file_and_dead_api_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated outage", request=request)

    with pytest.raises(PolicyLoadError):
        _runtime(tmp_path, handler, mounted=False)


def test_corrupt_mounted_file_falls_back_to_http(tmp_path):
    path = tmp_path / "policy-store.json"
    path.write_text("{not json", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_active_body(3))

    runtime = PolicyRuntime(
        mounted_file=path,
        policy_url="http://automation:8020",
        transport=httpx.MockTransport(handler),
    )

    assert runtime.snapshot().version == 3


# ---------- refresh ----------


def test_refresh_sends_if_none_match_and_applies_v2(tmp_path):
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["if_none_match"] = request.headers.get("If-None-Match")
        return httpx.Response(200, json=_active_body(2, waiting_bonus_points=20))

    runtime = _runtime(tmp_path, handler, version=1)

    assert runtime.refresh_once() is True
    assert seen["if_none_match"] == '"policy-v1"'
    assert runtime.snapshot().version == 2
    assert runtime.queue_policy().waiting_bonus_points == 20
    assert _loaded_version() == 2


def test_304_keeps_snapshot(tmp_path):
    runtime = _runtime(tmp_path, lambda request: httpx.Response(304), version=1)
    before = runtime.snapshot()

    assert runtime.refresh_once() is False
    # D4：snapshot() 按引用返回，所以同一性可以字面断言。
    assert runtime.snapshot() is before


def test_network_error_keeps_v1_and_counts_network(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=request)

    runtime = _runtime(tmp_path, handler, version=1)
    before = _failure_count("network")

    assert runtime.refresh_once() is False
    assert runtime.snapshot().version == 1
    assert _failure_count("network") - before == 1


def test_timeout_counts_as_network(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    runtime = _runtime(tmp_path, handler, version=1)
    before = _failure_count("network")

    assert runtime.refresh_once() is False  # 不得抛异常
    assert _failure_count("network") - before == 1


def test_http_500_keeps_v1_and_counts_http(tmp_path):
    runtime = _runtime(tmp_path, lambda request: httpx.Response(500), version=1)
    before = _failure_count("http")

    assert runtime.refresh_once() is False
    assert runtime.snapshot().version == 1
    assert _failure_count("http") - before == 1


def test_invalid_v2_keeps_v1_and_counts_validation(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        # waiting_bonus_interval_seconds 有 ge=1 约束
        return httpx.Response(200, json=_active_body(2, waiting_bonus_interval_seconds=0))

    runtime = _runtime(tmp_path, handler, version=1)
    before = _failure_count("validation")

    assert runtime.refresh_once() is False
    assert runtime.snapshot().version == 1
    assert runtime.queue_policy() == V1_QUEUE
    assert _failure_count("validation") - before == 1


def test_malformed_body_counts_validation(tmp_path):
    runtime = _runtime(tmp_path, lambda request: httpx.Response(200, text="not json"), version=1)
    before = _failure_count("validation")

    assert runtime.refresh_once() is False
    assert runtime.snapshot().version == 1
    assert _failure_count("validation") - before == 1


# ---------- 配置与线程 ----------


def test_refresh_seconds_defaults_to_5(tmp_path, monkeypatch):
    monkeypatch.delenv("POLICY_REFRESH_SECONDS", raising=False)
    runtime = _runtime(tmp_path)

    assert runtime.refresh_seconds == 5


def test_refresh_seconds_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_REFRESH_SECONDS", "11")
    runtime = PolicyRuntime(mounted_file=_store_file(tmp_path, 1), policy_url="http://x:8020")

    assert runtime.refresh_seconds == 11


def test_refresh_thread_stops_on_stop_event(tmp_path):
    hit = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        hit.set()
        return httpx.Response(304)

    runtime = _runtime(tmp_path, handler, version=1, refresh_seconds=0.05)
    stop_event = threading.Event()
    thread = runtime.start(stop_event)

    assert hit.wait(timeout=3), "刷新线程没有发出请求"
    stop_event.set()
    runtime.join(timeout=3)

    assert not thread.is_alive()


def test_refresh_thread_survives_unexpected_error(tmp_path):
    calls = {"n": 0}
    recovered = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("unexpected non-httpx failure")
        recovered.set()
        return httpx.Response(304)

    runtime = _runtime(tmp_path, handler, version=1, refresh_seconds=0.05)
    stop_event = threading.Event()
    runtime.start(stop_event)

    assert recovered.wait(timeout=3), "线程在一次意外异常后就死了"
    stop_event.set()
    runtime.join(timeout=3)


def test_metrics_exposition_contains_loaded_version(tmp_path):
    from prometheus_client import generate_latest

    _runtime(tmp_path, version=1)

    assert "polygate_policy_loaded_version" in generate_latest().decode()
