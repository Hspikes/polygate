"""
Redis 版本的 AutomationStore。

实现方式和 store.py 里的 AutomationStore 协议完全一致，
API 代码 (main.py) 不需要改任何一行——只需要在创建 app 的时候，
把 InMemoryAutomationStore() 换成 RedisAutomationStore(redis_client)。

Redis key 设计（都带 "automation:" 前缀，跟 Provider 层的熔断器状态、
网关的精确缓存共用同一个 Redis 实例，用前缀隔离，互不干扰）：

  automation:preview:{preview_id}   -> JSON (PreviewResponse)，TTL = expires_in_seconds
  automation:job:{job_id}           -> JSON (JobRecord 当前状态)
  automation:job-payload:{job_id}   -> JSON (完整 gateway_request，仅 Worker 可读，不对外暴露)
  automation:idempotency:{key}      -> job_id 字符串，TTL 24 小时
  automation:queue:pending          -> Redis Set，待处理任务（按 effective_priority 挑选，不是 FIFO）
  automation:queue:leases           -> Redis Sorted Set，score = 租约到期时间戳
  automation:queue:streak           -> 计数器，连续选中 critical/high 的次数（防饥饿机制用）
  automation:jobs:index             -> Redis Sorted Set，score = created_at，供 list_jobs() 排序
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

import redis

from automation.app.models import JobRecord, JobState, PreviewResponse

PREVIEW_TTL_SECONDS_DEFAULT = 900
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
# 内部执行快照的兜底 TTL——正常情况下任务完成/失败后会被主动清理，
# 这个 TTL 只是防止异常情况下（比如 Worker 一直没处理）快照永远堆在 Redis 里。
EXECUTION_PAYLOAD_TTL_SECONDS = 7 * 24 * 60 * 60


class RedisAutomationStore:
    """实现和 InMemoryAutomationStore 一样的协议，API 层无感知替换。"""

    def __init__(self, redis_client: "redis.Redis", prefix: str = "automation"):
        self.r = redis_client
        self.prefix = prefix

    def _k(self, *parts: str) -> str:
        return ":".join([self.prefix, *parts])

    # ---------- preview ----------

    def save_preview(self, preview: PreviewResponse) -> None:
        key = self._k("preview", preview.preview_id)
        self.r.set(key, preview.model_dump_json(), ex=preview.expires_in_seconds or PREVIEW_TTL_SECONDS_DEFAULT)

    def get_preview(self, preview_id: str) -> PreviewResponse | None:
        raw = self.r.get(self._k("preview", preview_id))
        if raw is None:
            return None
        return PreviewResponse.model_validate_json(raw)

    # ---------- job ----------

    def enqueue(self, preview: PreviewResponse, idempotency_key: str) -> JobRecord:
        idem_key = self._k("idempotency", idempotency_key)

        # 幂等：SET ... NX 保证并发请求下，同一个 idempotency_key 只有一个能真正入队
        job_id = "job_" + uuid.uuid4().hex
        created = self.r.set(idem_key, job_id, nx=True, ex=IDEMPOTENCY_TTL_SECONDS)
        if not created:
            existing_id = self.r.get(idem_key)
            existing_id = existing_id.decode() if isinstance(existing_id, bytes) else existing_id
            existing = self.get_job(existing_id) if existing_id else None
            if existing is not None:
                return existing
            job_id = "job_" + uuid.uuid4().hex  # 兜底：idempotency 记录还在但 job 数据丢了

        queue_position = self.r.scard(self._k("queue", "pending")) + 1
        record = JobRecord(
            job_id=job_id,
            status=JobState.queued,
            priority=preview.priority,
            queue_position=queue_position,
            created_at=datetime.now(UTC),
        )
        self._save_job(record)

        # 关键点：完整的 gateway_request（包含用户 prompt）只存在这个"内部专用"
        # 的 key 里，不放进公开的 JobRecord。GET /v1/jobs、GET /v1/jobs/{id}
        # 这些对外接口读的是 JobRecord，天然不会暴露这份内容；只有 Worker
        # 会主动读取这个 key。同时这份快照是入队时就写死的，不依赖会过期的
        # PreviewResponse，Worker 排多久队都不受影响。
        self.r.set(
            self._k("job-payload", record.job_id),
            preview.gateway_request.model_dump_json(),
            ex=EXECUTION_PAYLOAD_TTL_SECONDS,
        )

        # 待处理队列现在用 Set 而不是 List：因为要按 effective_priority
        # （见 claim_next_job）挑选，不是单纯先进先出，所以顺序无所谓，
        # 只需要能快速"取出全部待处理任务"。
        self.r.sadd(self._k("queue", "pending"), job_id)
        self.r.zadd(self._k("jobs", "index"), {job_id: record.created_at.timestamp()})
        return record

    def get_job(self, job_id: str) -> JobRecord | None:
        raw = self.r.get(self._k("job", job_id))
        if raw is None:
            return None
        return JobRecord.model_validate_json(raw)

    def list_jobs(self, status: JobState | None = None, limit: int = 100) -> list[JobRecord]:
        job_ids = self.r.zrevrange(self._k("jobs", "index"), 0, -1)
        records: list[JobRecord] = []
        for raw_id in job_ids:
            job_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            record = self.get_job(job_id)
            if record is None:
                continue
            if status is not None and record.status != status:
                continue
            records.append(record)
            if len(records) >= limit:
                break
        return records

    # ---------- 以下方法只给 Worker 用，API 不会调用 ----------

    def _save_job(self, record: JobRecord) -> None:
        self.r.set(self._k("job", record.job_id), record.model_dump_json())

    def get_execution_payload(self, job_id: str) -> dict | None:
        """Worker 专用：读取入队时存好的内部执行快照（完整 gateway_request）。
        这个方法不属于 AutomationStore 协议，API 代码不会调用它。"""
        raw = self.r.get(self._k("job-payload", job_id))
        if raw is None:
            return None
        return json.loads(raw)

    def _clear_execution_payload(self, job_id: str) -> None:
        """任务完成/失败后，主动清理掉这份内部快照，减少 prompt 在 Redis 里
        停留的时间（安全考虑，不是必须，但更稳妥）。"""
        self.r.delete(self._k("job-payload", job_id))

    # ---------- 优先级队列（按 A 确认的方案实现，2026-07-23 对齐） ----------
    #
    # effective_priority = initial_score + waiting_bonus
    #   waiting_bonus：每等待 5 秒 +1 分，最多 +30 分
    # 同分按创建时间 FIFO（更早创建的优先）
    # 防饥饿：连续选中 3 个 critical/high 之后，如果有 normal/low 已经等了
    #   至少 20 秒，强制选等待最久的那个（忽略 effective_priority 排名），
    #   选完之后 streak 清零
    #
    # 注意：这里假设 PriorityDecision 的字段名是 `class_`（因为 `class` 是
    # Python 关键字，猜测和 Snippets 里 `json_` 的命名方式一致）和
    # `initial_score`——这个假设需要对照实际的 PriorityDecision 模型源码
    # 核实一下，如果字段名不对，下面这几行要跟着改。
    _CRITICAL_HIGH = {"critical", "high"}
    _WAITING_BONUS_CAP = 30
    _WAITING_BONUS_PER_SECONDS = 5
    _STARVATION_STREAK_THRESHOLD = 3
    _STARVATION_WAIT_SECONDS = 20

    def claim_next_job(self, lease_seconds: int = 60) -> JobRecord | None:
        """Worker 每次调用（大约每 1 秒一次"准入窗口"）时，从当前所有排队中
        的任务里，按 effective_priority 选出一个执行，并附带防饥饿机制。"""
        pending_ids = self.r.smembers(self._k("queue", "pending"))
        if not pending_ids:
            return None

        now = time.time()
        candidates = []  # (job_id, record, priority_class, effective_priority, created_ts, waited_seconds)
        for raw_id in pending_ids:
            job_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            record = self.get_job(job_id)
            if record is None or record.status != JobState.queued:
                self.r.srem(self._k("queue", "pending"), job_id)  # 脏数据，顺手清掉
                continue
            priority_class = getattr(record.priority, "class_", None)
            initial_score = getattr(record.priority, "initial_score", 0)
            created_ts = record.created_at.timestamp()
            waited = now - created_ts
            waiting_bonus = min(self._WAITING_BONUS_CAP, int(waited // self._WAITING_BONUS_PER_SECONDS))
            effective_priority = initial_score + waiting_bonus
            candidates.append((job_id, record, priority_class, effective_priority, created_ts, waited))

        if not candidates:
            return None

        streak = int(self.r.get(self._k("queue", "streak")) or 0)

        chosen = None
        if streak >= self._STARVATION_STREAK_THRESHOLD:
            starved = [
                c for c in candidates
                if c[2] not in self._CRITICAL_HIGH and c[5] >= self._STARVATION_WAIT_SECONDS
            ]
            if starved:
                chosen = max(starved, key=lambda c: c[5])  # 等待最久的那个

        if chosen is None:
            # 正常情况：effective_priority 最高者胜出；同分按创建时间 FIFO
            chosen = max(candidates, key=lambda c: (c[3], -c[4]))

        job_id, record, priority_class, _, _, _ = chosen
        self.r.srem(self._k("queue", "pending"), job_id)

        if priority_class in self._CRITICAL_HIGH:
            self.r.incr(self._k("queue", "streak"))
        else:
            self.r.set(self._k("queue", "streak"), 0)

        record.status = JobState.running
        record.started_at = datetime.now(UTC)
        self._save_job(record)
        self.r.zadd(self._k("queue", "leases"), {job_id: time.time() + lease_seconds})
        return record

    def renew_lease(self, job_id: str, lease_seconds: int = 60) -> None:
        """长任务执行中定期调用，避免租约过期被别的 Worker 误抢。"""
        self.r.zadd(self._k("queue", "leases"), {job_id: time.time() + lease_seconds})

    def complete_job(self, job_id: str, result: dict) -> None:
        record = self.get_job(job_id)
        if record is None:
            return
        record.status = JobState.completed
        record.completed_at = datetime.now(UTC)
        record.result = result
        self._save_job(record)
        self.r.zrem(self._k("queue", "leases"), job_id)
        self._clear_execution_payload(job_id)

    def fail_job(self, job_id: str, error: str) -> None:
        """标记为最终失败（重试次数已用完）。注意：这个方法才清理执行快照；
        requeue_job()（还会重试）不清理，因为下一次重试还需要用到这份内容。"""
        record = self.get_job(job_id)
        if record is None:
            return
        record.status = JobState.failed
        record.completed_at = datetime.now(UTC)
        record.error = error
        self._save_job(record)
        self.r.zrem(self._k("queue", "leases"), job_id)
        self._clear_execution_payload(job_id)

    def requeue_job(self, job_id: str) -> None:
        """租约过期但任务没做完（比如 Worker 被强制杀死），重新放回待处理集合重试。
        注意：重新入队不重置 created_at，等待加成（waiting_bonus）继续按最初
        创建时间累积，这样重试的任务不会因为"重新计时"而丢失已经攒的优先级。"""
        record = self.get_job(job_id)
        if record is None:
            return
        record.status = JobState.queued
        self._save_job(record)
        self.r.zrem(self._k("queue", "leases"), job_id)
        self.r.sadd(self._k("queue", "pending"), job_id)

    def reap_expired_leases(self) -> list[str]:
        """扫描已过期但没完成的租约，重新入队，返回被重新入队的 job_id 列表。"""
        now = time.time()
        expired = self.r.zrangebyscore(self._k("queue", "leases"), 0, now)
        job_ids = [j.decode() if isinstance(j, bytes) else j for j in expired]
        for job_id in job_ids:
            self.requeue_job(job_id)
        return job_ids