from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

from automation.app.models import JobRecord, JobState, PreviewResponse


class AutomationStore(Protocol):
    """Persistence seam owned by B; API code depends only on this contract."""

    def save_preview(self, preview: PreviewResponse) -> None: ...

    def get_preview(self, preview_id: str) -> PreviewResponse | None: ...

    def enqueue(self, preview: PreviewResponse, idempotency_key: str) -> JobRecord: ...

    def get_job(self, job_id: str) -> JobRecord | None: ...

    def list_jobs(self, status: JobState | None = None, limit: int = 100) -> list[JobRecord]: ...


class InMemoryAutomationStore:
    """Functional local skeleton; B replaces this with Redis without changing API routes."""

    def __init__(self) -> None:
        self.previews: dict[str, PreviewResponse] = {}
        self.jobs: dict[str, JobRecord] = {}
        self.idempotency: dict[str, str] = {}

    def save_preview(self, preview: PreviewResponse) -> None:
        self.previews[preview.preview_id] = preview

    def get_preview(self, preview_id: str) -> PreviewResponse | None:
        return self.previews.get(preview_id)

    def enqueue(self, preview: PreviewResponse, idempotency_key: str) -> JobRecord:
        existing_id = self.idempotency.get(idempotency_key)
        if existing_id is not None:
            return self.jobs[existing_id]

        queued_count = sum(job.status == JobState.queued for job in self.jobs.values())
        record = JobRecord(
            job_id="job_" + uuid.uuid4().hex,
            status=JobState.queued,
            priority=preview.priority,
            queue_position=queued_count + 1,
            created_at=datetime.now(UTC),
            policy_version=preview.policy_version,
        )
        self.jobs[record.job_id] = record
        self.idempotency[idempotency_key] = record.job_id
        return record

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    def list_jobs(self, status: JobState | None = None, limit: int = 100) -> list[JobRecord]:
        records = sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)
        if status is not None:
            records = [job for job in records if job.status == status]
        return records[:limit]
