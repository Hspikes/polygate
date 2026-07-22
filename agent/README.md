# PolyGate Pi Agent integration boundary

Member D owns this directory. The Agent is a replaceable interaction layer;
company policy and Provider routing must not be implemented here.

## Runtime boundary

The Agent Service uses its own OpenAI-compatible model configuration. Its Pi
Extension calls the internal Automation API at `AUTOMATION_URL` (default
`http://automation:8020`). Pi's internal model traffic does not pass through
the PolyGate Gateway in this release.

## Required Pi tools

### `preview_polygate_request`

- Arguments: the exact object defined by
  `contracts/automation-intent.schema.json`.
- HTTP mapping: `POST /v1/requests/preview`.
- Result: the exact Automation Preview object, including generated JSON,
  curl, Python, priority reason, and policy adjustments.
- Safety: never submits a Job and never calls a Provider.

### `submit_polygate_job`

- Arguments: `preview_id`, `confirmed`, and a client-generated
  `idempotency_key`.
- HTTP mapping: `POST /v1/jobs` with the `Idempotency-Key` header.
- Result: the exact object defined by `automation-job.schema.json`.
- Safety: refuse to call the API unless the user has explicitly confirmed the
  currently displayed Preview.

### `get_polygate_job`

- Arguments: `job_id`.
- HTTP mapping: `GET /v1/jobs/{job_id}`.
- Result: the queued/running/completed/failed Job object. A completed result
  includes the existing Gateway response and decision card.

## Chat API expected by Web

`POST /agent/chat` accepts a `session_id`, message, and optional structured
requirement card. It streams SSE events with these stable names:

```text
message.delta
tool.call
tool.result
job.status
done
error
```

The browser continues to collect privacy, budget, urgency, and department
through explicit controls. The Agent must not infer those values from prose.

## Files D can add without backend conflicts

```text
agent/package.json
agent/tsconfig.json
agent/src/server.ts
agent/src/polygate-extension.ts
agent/src/automation-client.ts
agent/tests/**
web/**
```
