# Agent Automation Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the cross-team Automation API and provide a runnable, tested service skeleton so Members A, B, C, and D can develop in parallel without changing the existing Gateway contract.

**Architecture:** Add a standalone `automation/` FastAPI control-plane service. Its public models mirror version-controlled JSON Schemas in `contracts/`; queue, worker, Pi extension, UI, and Kubernetes work consume these contracts but remain separate implementation slices.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, JSON Schema draft-07, Docker, unittest.

## Global Constraints

- Do not modify the existing `/v1/chat/completions` request or decision-card contracts.
- Do not add Pi, queue, Provider, or Kubernetes behavior to the Gateway process.
- The requirement card is manually filled; Agent code must not infer privacy, budget, or urgency.
- Preview performs no Provider call and has no cost.
- Finance privacy is locked to `high`.
- No credentials, prompts, employee names, or job IDs may be metric labels.
- Build EKS images as `linux/amd64`; runtime deployment is outside this skeleton task.

---

### Task 1: Freeze Shared Automation Contracts

**Files:**
- Create: `contracts/automation-intent.schema.json`
- Create: `contracts/automation-preview.schema.json`
- Create: `contracts/automation-job.schema.json`
- Create: `contracts/automation-examples.json`
- Modify: `contracts/README.md`
- Test: `scripts/tests/test-automation-contracts.py`

**Interfaces:**
- Consumes: existing Gateway request fields `model`, `messages`, and `polygate`.
- Produces: exact enums, payload fields, status transitions, and examples consumed by every later task.

- [x] Write a standard-library contract test that requires the three schemas, checks their draft-07 metadata and required fields, validates enum/default consistency, and checks examples against the schemas.
- [x] Run `python3 scripts/tests/test-automation-contracts.py` and verify it fails because the schemas do not exist.
- [x] Add the schemas and examples with no implementation-only fields.
- [x] Run the test and verify it passes.

### Task 2: Build a Runnable Automation Service Boundary

**Files:**
- Create: `automation/app/__init__.py`
- Create: `automation/app/models.py`
- Create: `automation/app/templates.py`
- Create: `automation/app/main.py`
- Create: `automation/tests/test_api.py`
- Create: `automation/requirements.txt`
- Create: `automation/Dockerfile`
- Create: `automation/README.md`

**Interfaces:**
- Consumes: Task 1 schemas.
- Produces: `/health`, `/v1/templates`, `/v1/requests/preview`, `/v1/jobs`, and `/v1/jobs/{job_id}` with stable OpenAPI shapes.

- [x] Write API tests for health, four template IDs, Finance privacy locking, deterministic preview compilation, generated snippets, idempotent job submission, and job lookup.
- [x] Run the containerized test and verify it fails because the service does not exist.
- [x] Implement minimal in-memory preview/job storage with TTL metadata and explicit repository seams; do not implement Redis scheduling yet.
- [x] Run the tests and verify they pass.
- [x] Build the Docker image and verify `/health` from the container.

### Task 3: Publish Parallel Work Boundaries

**Files:**
- Create: `agent/README.md`
- Create: `docs/PolyGate_Agent并行开发对接清单.md`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: Task 1 API fields and Task 2 endpoint behavior.
- Produces: exact ownership and integration seams for A/B/C/D, plus a local Automation service reachable as `http://automation:8020`.

- [x] Document Pi tool names and argument/result mappings without implementing D's Agent code.
- [x] Document disjoint write scopes, PR order, branch starting point, and Day 1 integration checks.
- [x] Add only the Automation API service to Compose; leave Worker and Agent as separately owned follow-up services.
- [x] Run `docker compose config` and verify the rendered configuration.

### Task 4: Skeleton Regression Gate

**Files:**
- Create: `scripts/automation-skeleton-smoke-test.sh`

**Interfaces:**
- Consumes: local Automation API at `AUTOMATION_URL`.
- Produces: a repeatable health/templates/preview/privacy/idempotency smoke test for later team integration.

- [x] Write the smoke script against the frozen payloads.
- [x] Run shell syntax and whitespace checks.
- [x] Start Automation through Compose and run the smoke test.
- [x] Run the existing Gateway test/smoke checks that are available locally and confirm no contract regression.
- [x] Review `git diff` and provide exact commit/push guidance without staging or pushing automatically.
