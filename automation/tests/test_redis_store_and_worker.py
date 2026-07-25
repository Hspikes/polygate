"""
Redis Store 和 Worker 的自动化测试。

覆盖范围（对应团队 review 里明确要求的几项）：
- 幂等：同一个 Idempotency-Key 重复提交，只会真正入队一次
- 优先级排序：effective_priority 更高的任务先被 claim
- 防饥饿（fairness）：连续选中 3 个 critical/high 之后，等待够久的
  normal/low 任务会被强制插队
- Worker 成功执行：状态从 queued -> running -> completed
- Worker 重试：暂时性失败会重新入队，重试次数用完才最终标记 failed
- Bearer Key：POLYGATE_API_KEY 有值才携带 Authorization 头

运行前提：本地/CI 有一个可访问的 Redis 实例。默认用 DB 15（一个不太可能
和别的用途冲突的编号），每个测试前后都会 FLUSHDB，不会影响 DB 0 的真实数据。
"""
from __future__ import annotations

import os
import time
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import redis

os.environ.setdefault("AUTOMATION_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")

from automation.app.models import (
    GatewayMessage,
    GatewayRequest,
    Preferences,
    PriorityDecision,
    PreviewResponse,
    Snippets,
)
from automation.app.redis_store import RedisAutomationStore
from automation.app import worker as worker_module


def _make_preview(preview_id: str, priority_class: str, initial_score: int) -> PreviewResponse:
    prefs = Preferences(quality="cheap", privacy="standard", max_cost_usd=1.0, latency_target_ms=5000)
    gateway_request = GatewayRequest(
        model="auto",
        messages=[GatewayMessage(role="user", content=f"test {preview_id}")],
        polygate=prefs,
    )
    snippets = Snippets(json="{}", curl="curl ...", python="print(1)")
    priority = PriorityDecision(**{
        "class": priority_class,
        "initial_score": initial_score,
        "reason": "test",
    })
    return PreviewResponse(
        preview_id=preview_id,
        expires_in_seconds=900,
        normalized_intent={
            "employee": "tester",
            "department": "engineering",
            "scenario": "production_incident",
            "urgency": priority_class,
            "prompt": "test",
            "preferences": prefs.model_dump(),
        },
        priority=priority,
        gateway_request=gateway_request,
        snippets=snippets,
        policy_adjustments=[],
    )


class RedisStoreTestBase(unittest.TestCase):
    def setUp(self):
        redis_url = os.environ["AUTOMATION_TEST_REDIS_URL"]
        self.client = redis.Redis.from_url(redis_url)
        self.client.flushdb()
        self.store = RedisAutomationStore(self.client, prefix="automation_test")

    def tearDown(self):
        self.client.flushdb()


class IdempotencyTest(RedisStoreTestBase):
    def test_same_key_returns_same_job(self):
        preview = _make_preview("preview_1", "normal", 50)
        self.store.save_preview(preview)

        first = self.store.enqueue(preview, idempotency_key="same-key")
        second = self.store.enqueue(preview, idempotency_key="same-key")

        self.assertEqual(first.job_id, second.job_id)
        # 只应该真正入队一次
        self.assertEqual(self.store.queue_depth(), 1)

    def test_different_keys_create_different_jobs(self):
        preview = _make_preview("preview_2", "normal", 50)
        self.store.save_preview(preview)

        first = self.store.enqueue(preview, idempotency_key="key-a")
        second = self.store.enqueue(preview, idempotency_key="key-b")

        self.assertNotEqual(first.job_id, second.job_id)
        self.assertEqual(self.store.queue_depth(), 2)

    def test_policy_version_survives_idempotency_and_worker_state_transitions(self):
        preview = _make_preview("preview_policy_version", "normal", 50)
        preview.policy_version = 7

        first = self.store.enqueue(preview, idempotency_key="policy-version-key")
        repeated = self.store.enqueue(preview, idempotency_key="policy-version-key")
        self.assertEqual(first.policy_version, 7)
        self.assertEqual(repeated.policy_version, 7)

        running = self.store.claim_next_job(lease_seconds=60)
        self.assertEqual(running.policy_version, 7)

        self.store.complete_job(running.job_id, {"answer": "done"})
        completed = self.store.get_job(running.job_id)
        self.assertEqual(completed.policy_version, 7)


class PriorityOrderingTest(RedisStoreTestBase):
    def test_higher_score_claimed_first(self):
        low = _make_preview("preview_low", "low", 10)
        high = _make_preview("preview_high", "critical", 140)
        self.store.save_preview(low)
        self.store.save_preview(high)

        self.store.enqueue(low, idempotency_key="low-key")
        self.store.enqueue(high, idempotency_key="high-key")

        claimed = self.store.claim_next_job(lease_seconds=60)
        self.assertEqual(claimed.priority.initial_score, 140)


class FairnessStarvationTest(RedisStoreTestBase):
    def test_low_priority_forced_after_streak_and_wait(self):
        # 连续放 3 个 critical 任务，模拟"已经连续执行了 3 个 critical/high"
        for i in range(3):
            preview = _make_preview(f"preview_high_{i}", "critical", 140)
            self.store.save_preview(preview)
            self.store.enqueue(preview, idempotency_key=f"high-key-{i}")
            claimed = self.store.claim_next_job(lease_seconds=60)
            self.assertEqual(claimed.priority.class_.value, "critical")

        streak = int(self.client.get(self.store._k("queue", "streak")) or 0)
        self.assertGreaterEqual(streak, 3)

        # 现在放一个 low 任务，但把它的 created_at 手动改到 25 秒前，
        # 模拟"已经等了超过 20 秒"
        low_preview = _make_preview("preview_low_starved", "low", 5)
        self.store.save_preview(low_preview)
        low_job = self.store.enqueue(low_preview, idempotency_key="low-starved-key")
        low_job.created_at = datetime.now(UTC) - timedelta(seconds=25)
        self.store._save_job(low_job)

        # 再放一个 critical 任务，正常情况下分数更高应该被选中，
        # 但防饥饿机制应该强制选中等待够久的 low 任务
        high_preview = _make_preview("preview_high_again", "critical", 140)
        self.store.save_preview(high_preview)
        self.store.enqueue(high_preview, idempotency_key="high-key-again")

        claimed = self.store.claim_next_job(lease_seconds=60)
        self.assertEqual(claimed.job_id, low_job.job_id, "防饥饿机制应该强制选中等待够久的低优先级任务")

        # 执行完低优先级任务后，streak 应该清零
        streak_after = int(self.client.get(self.store._k("queue", "streak")) or 0)
        self.assertEqual(streak_after, 0)


class ExecutionPayloadTest(RedisStoreTestBase):
    def test_payload_saved_and_cleared_after_completion(self):
        preview = _make_preview("preview_payload", "normal", 50)
        self.store.save_preview(preview)
        record = self.store.enqueue(preview, idempotency_key="payload-key")

        payload = self.store.get_execution_payload(record.job_id)
        self.assertIsNotNone(payload)
        self.assertIn("messages", payload)

        self.store.complete_job(record.job_id, {"answer": "done"})
        self.assertIsNone(self.store.get_execution_payload(record.job_id))


class WorkerExecuteJobTest(RedisStoreTestBase):
    def setUp(self):
        super().setUp()
        worker_module._retry_counts.clear()

    def _enqueue_one(self, idem_key="job-key"):
        preview = _make_preview("preview_exec", "normal", 50)
        self.store.save_preview(preview)
        self.store.enqueue(preview, idempotency_key=idem_key)
        return self.store.claim_next_job(lease_seconds=60)

    @patch("automation.app.worker.httpx.Client")
    def test_success_marks_job_completed(self, mock_client_cls):
        job = self._enqueue_one()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"answer": "hello"}
        mock_resp.raise_for_status.return_value = None
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp

        worker_module.execute_job(self.store, job)

        updated = self.store.get_job(job.job_id)
        self.assertEqual(updated.status.value, "completed")
        self.assertEqual(updated.result["answer"], "hello")

    @patch("automation.app.worker.httpx.Client")
    def test_transient_failure_retries_then_eventually_fails(self, mock_client_cls):
        job = self._enqueue_one(idem_key="job-key-retry")

        mock_client_cls.return_value.__enter__.return_value.post.side_effect = RuntimeError("boom")

        # MAX_RETRIES 次重试之后应该最终失败
        for _ in range(worker_module.MAX_RETRIES + 1):
            current = self.store.get_job(job.job_id)
            if current.status.value == "queued":
                current = self.store.claim_next_job(lease_seconds=60)
            worker_module.execute_job(self.store, current)

        final = self.store.get_job(job.job_id)
        self.assertEqual(final.status.value, "failed")
        self.assertIsNotNone(final.error)

    @patch("automation.app.worker.httpx.Client")
    def test_auth_failure_fails_immediately_without_retrying(self, mock_client_cls):
        job = self._enqueue_one(idem_key="job-key-auth-fail")

        import httpx as httpx_module
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        auth_error = httpx_module.HTTPStatusError("unauthorized", request=MagicMock(), response=mock_resp)
        mock_client_cls.return_value.__enter__.return_value.post.side_effect = auth_error

        # 只调用一次 execute_job，401 应该直接判失败，不应该走"重试"分支
        worker_module.execute_job(self.store, job)

        final = self.store.get_job(job.job_id)
        self.assertEqual(final.status.value, "failed", "认证失败应该直接判定为最终失败，不占用重试次数")
        self.assertEqual(worker_module._retry_counts.get(job.job_id, 0), 0, "认证失败不应该消耗任何重试次数")

    def _assert_status_code_retryable(self, status_code: int, expected_retryable: bool):
        """辅助方法：给定一个状态码，断言 _is_retryable() 的判断结果符合预期。"""
        import httpx as httpx_module
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        error = httpx_module.HTTPStatusError(f"status {status_code}", request=MagicMock(), response=mock_resp)
        self.assertEqual(
            worker_module._is_retryable(error), expected_retryable,
            f"状态码 {status_code} 的可重试判断不符合预期",
        )

    def test_non_retryable_status_codes_do_not_retry(self):
        # 客户端错误：请求本身有问题，重试没有意义
        for status_code in (400, 401, 403, 422):
            self._assert_status_code_retryable(status_code, expected_retryable=False)

    def test_retryable_status_codes_do_retry(self):
        # 超时/限流/服务端错误：属于暂时性问题，值得重试
        for status_code in (408, 429, 500, 502, 503):
            self._assert_status_code_retryable(status_code, expected_retryable=True)

    def test_network_level_errors_are_retryable(self):
        # 不是 HTTPStatusError 的情况（比如连接失败、超时异常），默认可以重试
        self.assertTrue(worker_module._is_retryable(RuntimeError("connection reset")))

    @patch("automation.app.worker.httpx.Client")
    def test_validation_error_400_fails_immediately(self, mock_client_cls):
        job = self._enqueue_one(idem_key="job-key-400")

        import httpx as httpx_module
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        error = httpx_module.HTTPStatusError("bad request", request=MagicMock(), response=mock_resp)
        mock_client_cls.return_value.__enter__.return_value.post.side_effect = error

        worker_module.execute_job(self.store, job)

        final = self.store.get_job(job.job_id)
        self.assertEqual(final.status.value, "failed", "400 参数错误应该直接判定为最终失败，不占用重试次数")
        self.assertEqual(worker_module._retry_counts.get(job.job_id, 0), 0)

    @patch("automation.app.worker.httpx.Client")
    def test_bearer_header_only_added_when_key_configured(self, mock_client_cls):
        job = self._enqueue_one(idem_key="job-key-bearer")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"answer": "hi"}
        mock_resp.raise_for_status.return_value = None
        post_mock = mock_client_cls.return_value.__enter__.return_value.post
        post_mock.return_value = mock_resp

        with patch.object(worker_module, "POLYGATE_API_KEY", "test-key-123"):
            worker_module.execute_job(self.store, job)
        _, kwargs = post_mock.call_args
        self.assertEqual(kwargs["headers"].get("Authorization"), "Bearer test-key-123")

        job2 = self._enqueue_one(idem_key="job-key-no-bearer")
        with patch.object(worker_module, "POLYGATE_API_KEY", ""):
            worker_module.execute_job(self.store, job2)
        _, kwargs2 = post_mock.call_args
        self.assertNotIn("Authorization", kwargs2["headers"])


if __name__ == "__main__":
    unittest.main()
