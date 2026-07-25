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
from automation.app.policy_models import QueuePolicy
from automation.app.redis_store import (
    DEFAULT_QUEUE_POLICY,
    RedisAutomationStore,
    waiting_bonus,
)
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
    def _drive_streak(self, count: int, queue_policy=None):
        """连续领走 count 个 critical 任务，把 streak 顶上去。"""
        for i in range(count):
            preview = _make_preview(f"preview_high_{i}", "critical", 140)
            self.store.save_preview(preview)
            self.store.enqueue(preview, idempotency_key=f"high-key-{i}")
            claimed = self.store.claim_next_job(lease_seconds=60, queue_policy=queue_policy)
            self.assertEqual(claimed.priority.class_.value, "critical")

    def _enqueue_starved_low(self, waited_seconds: int):
        low_preview = _make_preview("preview_low_starved", "low", 5)
        self.store.save_preview(low_preview)
        low_job = self.store.enqueue(low_preview, idempotency_key="low-starved-key")
        low_job.created_at = datetime.now(UTC) - timedelta(seconds=waited_seconds)
        self.store._save_job(low_job)
        return low_job

    def test_low_priority_forced_after_streak_and_wait_with_injected_policy(self):
        """注入的 starvation 参数应当立刻生效，而不是沿用 v1 的 3 次/20 秒。"""
        policy = QueuePolicy(
            waiting_bonus_interval_seconds=1,
            waiting_bonus_points=20,
            waiting_bonus_cap=100,
            starvation_streak_threshold=2,
            starvation_wait_seconds=3,
        )

        # 只驱动 2 个 critical——在 v1 默认（阈值 3）下还不足以触发防饥饿
        self._drive_streak(2, queue_policy=policy)
        streak = int(self.client.get(self.store._k("queue", "streak")) or 0)
        self.assertEqual(streak, 2)

        # 只等 5 秒——在 v1 默认（20 秒）下也不够
        low_job = self._enqueue_starved_low(waited_seconds=5)

        high_preview = _make_preview("preview_high_again", "critical", 140)
        self.store.save_preview(high_preview)
        self.store.enqueue(high_preview, idempotency_key="high-key-again")

        claimed = self.store.claim_next_job(lease_seconds=60, queue_policy=policy)
        self.assertEqual(claimed.job_id, low_job.job_id, "注入的防饥饿阈值应当立刻生效")

        streak_after = int(self.client.get(self.store._k("queue", "streak")) or 0)
        self.assertEqual(streak_after, 0)

    def test_v1_defaults_reproduce_legacy_fairness_behavior(self):
        """回归护栏：不传 queue_policy 时，行为必须与 Task 4 改造前逐位一致。

        这是整个 Task 4 最重要的一个测试——它证明参数化没有偷偷改变
        既有生产行为（3 次连续 critical + 等待 20 秒才触发防饥饿）。
        """
        self._drive_streak(3)
        streak = int(self.client.get(self.store._k("queue", "streak")) or 0)
        self.assertGreaterEqual(streak, 3)

        low_job = self._enqueue_starved_low(waited_seconds=25)

        high_preview = _make_preview("preview_high_again", "critical", 140)
        self.store.save_preview(high_preview)
        self.store.enqueue(high_preview, idempotency_key="high-key-again")

        claimed = self.store.claim_next_job(lease_seconds=60)
        self.assertEqual(claimed.job_id, low_job.job_id, "防饥饿机制应该强制选中等待够久的低优先级任务")

        streak_after = int(self.client.get(self.store._k("queue", "streak")) or 0)
        self.assertEqual(streak_after, 0)

    def test_starvation_below_injected_threshold_does_not_trigger(self):
        """阈值调高后，原本会触发的 streak 不应再触发覆盖。"""
        policy = QueuePolicy(
            waiting_bonus_interval_seconds=5,
            waiting_bonus_points=1,
            waiting_bonus_cap=30,
            starvation_streak_threshold=10,
            starvation_wait_seconds=20,
        )
        self._drive_streak(3, queue_policy=policy)
        low_job = self._enqueue_starved_low(waited_seconds=25)

        high_preview = _make_preview("preview_high_again", "critical", 140)
        self.store.save_preview(high_preview)
        self.store.enqueue(high_preview, idempotency_key="high-key-again")

        claimed = self.store.claim_next_job(lease_seconds=60, queue_policy=policy)
        self.assertNotEqual(claimed.job_id, low_job.job_id)
        self.assertEqual(claimed.priority.class_.value, "critical")


class DynamicWaitingBonusTest(RedisStoreTestBase):
    def _enqueue_aged_low(self, waited_seconds: int):
        preview = _make_preview("preview_low_aged", "low", 10)
        self.store.save_preview(preview)
        job = self.store.enqueue(preview, idempotency_key="low-aged-key")
        job.created_at = datetime.now(UTC) - timedelta(seconds=waited_seconds)
        self.store._save_job(job)
        return job

    def _enqueue_fresh_critical(self):
        preview = _make_preview("preview_critical_fresh", "critical", 140)
        self.store.save_preview(preview)
        return self.store.enqueue(preview, idempotency_key="critical-fresh-key")

    def test_waiting_bonus_points_change_claim_order(self):
        low_job = self._enqueue_aged_low(waited_seconds=10)
        critical_job = self._enqueue_fresh_critical()

        # v1 默认：low = 10 + min(30, 1 * 2) = 12，critical = 140 胜出
        generous = QueuePolicy(
            waiting_bonus_interval_seconds=1,
            waiting_bonus_points=20,
            waiting_bonus_cap=1000,
            starvation_streak_threshold=3,
            starvation_wait_seconds=20,
        )
        # 调整后：low = 10 + min(1000, 20 * 10) = 210，反超 critical
        claimed = self.store.claim_next_job(lease_seconds=60, queue_policy=generous)
        self.assertEqual(claimed.job_id, low_job.job_id)

        # 已排队任务的 initial_score 不得被改写
        self.assertEqual(claimed.priority.initial_score, 10)
        self.assertEqual(self.store.get_job(critical_job.job_id).priority.initial_score, 140)

    def test_v1_defaults_keep_critical_first(self):
        """同样的两个任务，在 v1 默认参数下 critical 仍然胜出。"""
        self._enqueue_aged_low(waited_seconds=10)
        critical_job = self._enqueue_fresh_critical()

        claimed = self.store.claim_next_job(lease_seconds=60)
        self.assertEqual(claimed.job_id, critical_job.job_id)

    def test_waiting_bonus_cap_limits_bonus(self):
        """cap 的单位是分数，不是区间数：10 秒 × 10 分应被 cap 到 5。"""
        self._enqueue_aged_low(waited_seconds=10)
        critical_job = self._enqueue_fresh_critical()

        capped = QueuePolicy(
            waiting_bonus_interval_seconds=1,
            waiting_bonus_points=10,
            waiting_bonus_cap=5,
            starvation_streak_threshold=3,
            starvation_wait_seconds=20,
        )
        # low = 10 + 5 = 15 < 140
        claimed = self.store.claim_next_job(lease_seconds=60, queue_policy=capped)
        self.assertEqual(claimed.job_id, critical_job.job_id)


class WaitingBonusArithmeticTest(unittest.TestCase):
    """纯函数，不需要 Redis。"""

    def test_v1_defaults_reproduce_legacy_arithmetic(self):
        # 改造前是 min(30, int(waited // 5))
        for waited, expected in [(0, 0), (4.9, 0), (5, 1), (14, 2), (150, 30), (10_000, 30)]:
            with self.subTest(waited=waited):
                self.assertEqual(waiting_bonus(waited, DEFAULT_QUEUE_POLICY), expected)
                self.assertEqual(waiting_bonus(waited, DEFAULT_QUEUE_POLICY), min(30, int(waited // 5)))

    def test_points_multiply_intervals(self):
        policy = QueuePolicy(
            waiting_bonus_interval_seconds=2,
            waiting_bonus_points=7,
            waiting_bonus_cap=1000,
            starvation_streak_threshold=3,
            starvation_wait_seconds=20,
        )
        self.assertEqual(waiting_bonus(6, policy), 21)

    def test_negative_wait_is_clamped(self):
        """时钟偏斜或未来时间戳不应产生负 bonus。"""
        self.assertEqual(waiting_bonus(-100, DEFAULT_QUEUE_POLICY), 0)


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
