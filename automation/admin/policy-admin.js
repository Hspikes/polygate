(function () {
  "use strict";

  let adminKey = "";

  const QUALITY_OPTIONS = [
    { value: "cheap", label: "Cheap" },
    { value: "balanced", label: "Balanced" },
    { value: "high", label: "High" },
  ];
  const PRIVACY_OPTIONS = [
    { value: "standard", label: "Standard" },
    { value: "high", label: "High" },
  ];

  function domId(path) {
    return "policy-" + path.replaceAll(".", "-").replaceAll("_", "-");
  }

  function numberField(path, label, min, max, step, help, unit) {
    return Object.freeze({
      path,
      domId: domId(path),
      label,
      kind: "number",
      min,
      max,
      step,
      help,
      unit: unit || "",
      rangeLabel: `${min}–${max}${step === 1 ? " (whole number)" : ""}`,
      disabled: false,
    });
  }

  function selectField(path, label, options, help, disabled) {
    return Object.freeze({
      path,
      domId: domId(path),
      label,
      kind: "select",
      options,
      help: help || "",
      unit: "",
      rangeLabel: options.map((option) => option.value).join(" or "),
      disabled: Boolean(disabled),
    });
  }

  const GATEWAY_FIELDS = Object.freeze([
    numberField(
      "gateway.assumed_output_tokens",
      "Assumed output tokens",
      1,
      32768,
      1,
      "Token estimate used when the request does not provide a completion limit.",
      "tokens",
    ),
    numberField(
      "gateway.balanced_price_tolerance",
      "Balanced price tolerance",
      0,
      2,
      0.01,
      "Additional relative cost tolerated when balanced routing prefers a real provider.",
      "ratio",
    ),
    selectField(
      "gateway.budget_mode",
      "Budget mode",
      [
        { value: "soft", label: "Soft preference" },
        { value: "hard", label: "Hard limit" },
      ],
      "Choose whether request budgets guide or strictly constrain routing.",
    ),
    selectField(
      "gateway.latency_mode",
      "Latency mode",
      [
        { value: "soft", label: "Soft preference" },
        { value: "hard", label: "Hard limit" },
      ],
      "Choose whether latency targets guide or strictly constrain routing.",
    ),
    selectField(
      "gateway.high_quality_strategy",
      "High quality strategy",
      [
        { value: "prefer_real", label: "Prefer real provider" },
        { value: "lowest_cost", label: "Lowest cost" },
      ],
      "Define how high-quality requests choose between eligible providers.",
    ),
  ]);

  const URGENCY_FIELDS = Object.freeze([
    numberField("automation.urgency_scores.critical", "Critical", 0, 1000, 1, "", "points"),
    numberField("automation.urgency_scores.high", "High", 0, 1000, 1, "", "points"),
    numberField("automation.urgency_scores.normal", "Normal", 0, 1000, 1, "", "points"),
    numberField("automation.urgency_scores.low", "Low", 0, 1000, 1, "", "points"),
  ]);

  const QUEUE_FIELDS = Object.freeze([
    numberField(
      "automation.queue.waiting_bonus_interval_seconds",
      "Waiting bonus interval",
      1,
      3600,
      1,
      "Elapsed time between waiting-score rewards.",
      "seconds",
    ),
    numberField(
      "automation.queue.waiting_bonus_points",
      "Waiting bonus points",
      0,
      100,
      1,
      "Points added at each waiting interval.",
      "points",
    ),
    numberField(
      "automation.queue.waiting_bonus_cap",
      "Waiting bonus cap",
      0,
      1000,
      1,
      "Maximum accumulated waiting score; this is a point value, not an interval count.",
      "points",
    ),
    numberField(
      "automation.queue.starvation_streak_threshold",
      "Starvation streak threshold",
      1,
      100,
      1,
      "Consecutive selections before starvation protection applies.",
      "jobs",
    ),
    numberField(
      "automation.queue.starvation_wait_seconds",
      "Starvation wait",
      1,
      86400,
      1,
      "Wait duration that activates starvation protection.",
      "seconds",
    ),
  ]);

  function scenarioFields(key, financeLocked) {
    const prefix = `automation.scenarios.${key}`;
    return Object.freeze([
      numberField(`${prefix}.weight`, "Priority weight", 0, 500, 1, "Added to the urgency score.", "points"),
      selectField(`${prefix}.defaults.quality`, "Default quality", QUALITY_OPTIONS, ""),
      selectField(
        `${prefix}.defaults.privacy`,
        "Default privacy",
        financeLocked ? [{ value: "high", label: "High · locked" }] : PRIVACY_OPTIONS,
        financeLocked ? "Finance workloads always require high privacy." : "",
        financeLocked,
      ),
      numberField(`${prefix}.defaults.max_cost_usd`, "Maximum cost", 0, 10, 0.001, "Per request.", "USD"),
      numberField(
        `${prefix}.defaults.latency_target_ms`,
        "Latency target",
        1,
        120000,
        1,
        "Default routing target.",
        "ms",
      ),
    ]);
  }

  const SCENARIO_GROUPS = Object.freeze([
    { key: "production_incident", label: "Production incident", fields: scenarioFields("production_incident", false) },
    { key: "customer_escalation", label: "Customer escalation", fields: scenarioFields("customer_escalation", false) },
    { key: "finance_summary", label: "Finance summary", fields: scenarioFields("finance_summary", true) },
    { key: "marketing_batch", label: "Marketing batch", fields: scenarioFields("marketing_batch", false) },
  ]);

  const ALL_FIELDS = Object.freeze([
    ...GATEWAY_FIELDS,
    ...URGENCY_FIELDS,
    ...SCENARIO_GROUPS.flatMap((scenario) => scenario.fields),
    ...QUEUE_FIELDS,
  ]);
  const FIELD_BY_PATH = Object.freeze(
    Object.fromEntries(ALL_FIELDS.map((field) => [field.path, field])),
  );

  const GATEWAY_CASES = Object.freeze([
    {
      model: "auto",
      messages: [{ role: "user", content: "Summarize the incident response status." }],
      polygate: { quality: "balanced", privacy: "standard", max_cost_usd: 0.01, latency_target_ms: 1500 },
    },
    {
      model: "auto",
      messages: [{ role: "user", content: "Review a sensitive finance briefing." }],
      polygate: { quality: "high", privacy: "high", max_cost_usd: 0.03, latency_target_ms: 3000 },
    },
    {
      model: "auto",
      messages: [{ role: "user", content: "Generate low-cost marketing variants." }],
      polygate: { quality: "cheap", privacy: "standard", max_cost_usd: 0.003, latency_target_ms: 5000 },
    },
  ]);

  const PRIORITY_CASES = Object.freeze([
    {
      employee: "policy-preview",
      department: "engineering",
      scenario: "production_incident",
      urgency: "critical",
      prompt: "Restore a production service.",
      preferences: { quality: "high", privacy: "high", max_cost_usd: 0.03, latency_target_ms: 1000 },
    },
    {
      employee: "policy-preview",
      department: "support",
      scenario: "customer_escalation",
      urgency: "high",
      prompt: "Prepare an escalation response.",
      preferences: { quality: "balanced", privacy: "standard", max_cost_usd: 0.01, latency_target_ms: 1500 },
    },
    {
      employee: "policy-preview",
      department: "finance",
      scenario: "finance_summary",
      urgency: "normal",
      prompt: "Summarize the quarterly finance report.",
      preferences: { quality: "balanced", privacy: "high", max_cost_usd: 0.01, latency_target_ms: 3000 },
    },
    {
      employee: "policy-preview",
      department: "marketing",
      scenario: "marketing_batch",
      urgency: "low",
      prompt: "Create a batch of campaign ideas.",
      preferences: { quality: "cheap", privacy: "standard", max_cost_usd: 0.003, latency_target_ms: 5000 },
    },
  ]);

  class ApiError extends Error {
    constructor(status, code, message, details) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
      this.details = details || [];
    }
  }

  function clone(value) {
    return value === null || value === undefined ? value : JSON.parse(JSON.stringify(value));
  }

  function getAtPath(target, path) {
    return path.split(".").reduce((value, segment) => {
      if (value === null || value === undefined) return undefined;
      return value[segment];
    }, target);
  }

  function setAtPath(target, path, value) {
    const segments = path.split(".");
    let cursor = target;
    for (let index = 0; index < segments.length - 1; index += 1) {
      cursor = cursor[segments[index]];
    }
    cursor[segments[segments.length - 1]] = value;
    return target;
  }

  function invalidateRevisions(state) {
    state.draftRevision += 1;
    state.validatedRevision = null;
    state.previewedRevision = null;
    state.validation = null;
    state.preview = null;
    return state;
  }

  function canPreviewRevision(state) {
    return (
      state.busyAction === null &&
      state.validatedRevision === state.draftRevision &&
      state.validation !== null &&
      state.validation.valid === true
    );
  }

  function canPublishRevision(state) {
    const noteLength = state.changeNote.trim().length;
    return (
      state.busyAction === null &&
      state.validatedRevision === state.draftRevision &&
      state.previewedRevision === state.draftRevision &&
      state.preview !== null &&
      state.preview.base_version === state.baseVersion &&
      noteLength >= 1 &&
      noteLength <= 500
    );
  }

  function localUrgencyOrderError(draft) {
    const scores = getAtPath(draft, "automation.urgency_scores");
    if (!scores) return "Urgency scores are unavailable.";
    const values = [scores.critical, scores.high, scores.normal, scores.low];
    if (values.some((value) => typeof value !== "number" || !Number.isFinite(value))) return "";
    if (!(scores.critical > scores.high && scores.high > scores.normal && scores.normal > scores.low)) {
      return "Urgency scores must satisfy critical > high > normal > low.";
    }
    return "";
  }

  function normalizeValidationPath(location) {
    const segments = Array.isArray(location) ? location.map(String) : [];
    while (segments[0] === "body") segments.shift();
    if (segments[0] === "policy") segments.shift();
    return segments.join(".");
  }

  function mapValidationDetails(details) {
    if (!Array.isArray(details)) return [];
    return details.map((detail, index) => ({
      key: `${normalizeValidationPath(detail.loc)}-${index}`,
      path: normalizeValidationPath(detail.loc),
      message: typeof detail.msg === "string" ? detail.msg : "The server rejected this value.",
    }));
  }

  function diffObjects(before, after, path) {
    const currentPath = path || "";
    if (
      before !== null &&
      after !== null &&
      typeof before === "object" &&
      typeof after === "object" &&
      !Array.isArray(before) &&
      !Array.isArray(after)
    ) {
      const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort();
      return keys.flatMap((key) => {
        const childPath = currentPath ? `${currentPath}.${key}` : key;
        return diffObjects(before[key], after[key], childPath);
      });
    }
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      return [{ path: currentPath, before: clone(before), after: clone(after) }];
    }
    return [];
  }

  function validateLocalFields(draft) {
    const errors = {};
    if (!draft) return errors;
    ALL_FIELDS.forEach((field) => {
      const value = getAtPath(draft, field.path);
      if (field.kind === "number") {
        if (typeof value !== "number" || !Number.isFinite(value)) {
          errors[field.path] = `${field.label} is required and must be a number.`;
        } else if (value < field.min || value > field.max) {
          errors[field.path] = `${field.label} must be within ${field.rangeLabel}.`;
        } else if (field.step === 1 && !Number.isInteger(value)) {
          errors[field.path] = `${field.label} must be a whole number.`;
        }
      } else if (!field.options.some((option) => option.value === value)) {
        errors[field.path] = `${field.label} must be ${field.rangeLabel}.`;
      }
    });
    const financePath = "automation.scenarios.finance_summary.defaults.privacy";
    if (getAtPath(draft, financePath) !== "high") {
      errors[financePath] = "Finance summary privacy must remain high.";
    }
    return errors;
  }

  async function apiRequest(path, options) {
    const settings = options || {};
    const headers = { Accept: "application/json" };
    if (settings.authenticated) {
      if (!adminKey) throw new ApiError(401, "unauthorized", "Enter an administrator key to continue.");
      headers.Authorization = `Bearer ${adminKey}`;
    }
    if (settings.body !== undefined) headers["Content-Type"] = "application/json";

    let response;
    try {
      response = await fetch(path, {
        method: settings.method || "GET",
        headers,
        body: settings.body === undefined ? undefined : JSON.stringify(settings.body),
        cache: "no-store",
        credentials: "omit",
      });
    } catch (_) {
      throw new ApiError(0, "network", "The service could not be reached. Your draft is unchanged.");
    }

    if (response.status === 304) return { notModified: true, data: null, etag: response.headers.get("ETag") };
    if (response.status === 204) return { notModified: false, data: null, etag: response.headers.get("ETag") };

    const isJson = (response.headers.get("Content-Type") || "").includes("application/json");
    let payload = null;
    if (isJson) {
      try {
        payload = await response.json();
      } catch (_) {
        throw new ApiError(response.status, "protocol", "The service returned malformed JSON.");
      }
    }

    if (!response.ok) {
      if (response.status === 401) {
        throw new ApiError(401, "unauthorized", "The administrator key was rejected or expired.");
      }
      if (response.status === 404) {
        throw new ApiError(404, "not_found", "The selected policy version no longer exists.");
      }
      if (response.status === 409) {
        throw new ApiError(
          409,
          "conflict",
          "The active policy changed while you were editing. Reload the latest version and preview again.",
        );
      }
      if (response.status === 422) {
        throw new ApiError(
          422,
          "validation",
          "The server rejected one or more policy values.",
          mapValidationDetails(payload && payload.detail),
        );
      }
      if (response.status === 503) {
        throw new ApiError(503, "unavailable", "The policy service is temporarily unavailable. Your draft is unchanged.");
      }
      throw new ApiError(response.status, "http", "The policy service returned an unexpected error.");
    }

    if (!isJson) throw new ApiError(response.status, "protocol", "The service returned an unexpected response format.");
    return { notModified: false, data: payload, etag: response.headers.get("ETag") };
  }

  function createPolicyAdminApp() {
    return {
      connected: false,
      gateError: "",
      baseVersion: null,
      draftRevision: 1,
      validatedRevision: null,
      previewedRevision: null,
      activePolicy: null,
      draft: null,
      validation: null,
      preview: null,
      history: [],
      changeNote: "",
      busyAction: null,
      localFieldErrors: {},
      serverErrors: [],
      message: { kind: "info", text: "" },
      activeEtag: null,
      comparison: null,
      rollbackTarget: null,
      rollbackNote: "",
      gatewayFields: GATEWAY_FIELDS,
      urgencyFields: URGENCY_FIELDS,
      queueFields: QUEUE_FIELDS,
      scenarioGroups: SCENARIO_GROUPS,

      async init() {
        try {
          await this.loadActive(true);
        } catch (error) {
          this.message = { kind: "error", text: error.message };
        }
      },

      async connect(event) {
        const input = event.currentTarget.elements.credential;
        this.gateError = "";
        if (!input.value) {
          this.gateError = "Enter an administrator key to continue.";
          return;
        }
        adminKey = input.value;
        input.value = "";
        this.busyAction = "connect";
        try {
          await this.loadActive(true);
          await this.loadHistory();
          this.connected = true;
          this.message = { kind: "success", text: `Connected to active policy v${this.baseVersion}.` };
        } catch (error) {
          adminKey = "";
          this.connected = false;
          this.gateError = error.message;
        } finally {
          this.busyAction = null;
        }
      },

      disconnect() {
        adminKey = "";
        this.connected = false;
        this.history = [];
        this.validation = null;
        this.preview = null;
        this.comparison = null;
        this.rollbackTarget = null;
        this.changeNote = "";
        this.rollbackNote = "";
        this.serverErrors = [];
        this.message = { kind: "info", text: "Session disconnected." };
      },

      async loadActive(replaceDraft) {
        const result = await apiRequest("/v1/policies/active", { authenticated: false });
        if (result.notModified) return;
        this.activeEtag = result.etag;
        this.activePolicy = result.data;
        this.baseVersion = result.data.version;
        if (replaceDraft) {
          this.draft = clone(result.data.policy);
          this.draftRevision = 1;
          this.validatedRevision = null;
          this.previewedRevision = null;
          this.validation = null;
          this.preview = null;
          this.changeNote = "";
          this.serverErrors = [];
          this.localFieldErrors = validateLocalFields(this.draft);
          this.comparison = null;
          this.rollbackTarget = null;
        }
      },

      async loadHistory() {
        const result = await apiRequest("/v1/admin/policies", { authenticated: true });
        this.history = result.data.slice().sort((left, right) => right.version - left.version);
      },

      async reloadWorkspace() {
        this.busyAction = "reload";
        try {
          await this.loadActive(true);
          await this.loadHistory();
          this.message = { kind: "success", text: `Reloaded active policy v${this.baseVersion}.` };
        } catch (error) {
          this.handleActionError(error);
        } finally {
          this.busyAction = null;
        }
      },

      readField(path) {
        const value = getAtPath(this.draft, path);
        return value === null || value === undefined ? "" : value;
      },

      isSelected(path, value) {
        return getAtPath(this.draft, path) === value;
      },

      writeField(path, rawValue, field) {
        if (!this.draft || field.disabled) return;
        let value = rawValue;
        if (field.kind === "number") value = rawValue === "" ? null : Number(rawValue);
        setAtPath(this.draft, path, value);
        invalidateRevisions(this);
        this.serverErrors = [];
        this.localFieldErrors = validateLocalFields(this.draft);
        this.message = { kind: "info", text: "Draft changed. Validate and preview this revision before publishing." };
      },

      fieldError(path) {
        if (this.localFieldErrors[path]) return this.localFieldErrors[path];
        const match = this.serverErrors.find((error) => error.path === path);
        if (!match) return "";
        const field = FIELD_BY_PATH[path];
        return field ? `${match.message} Expected ${field.rangeLabel}.` : match.message;
      },

      updateChangeNote(event) {
        this.changeNote = event.target.value;
      },

      updateRollbackNote(event) {
        this.rollbackNote = event.target.value;
      },

      async validateDraft() {
        this.localFieldErrors = validateLocalFields(this.draft);
        if (Object.keys(this.localFieldErrors).length > 0 || this.urgencyOrderError) {
          this.message = { kind: "error", text: "Fix the highlighted local validation errors first." };
          return;
        }
        const revision = this.draftRevision;
        this.busyAction = "validate";
        this.serverErrors = [];
        try {
          const result = await apiRequest("/v1/admin/policies/validate", {
            method: "POST",
            authenticated: true,
            body: clone(this.draft),
          });
          if (!result.data || result.data.valid !== true) {
            throw new ApiError(200, "protocol", "Validation did not return a successful result.");
          }
          if (revision !== this.draftRevision) return;
          this.validation = result.data;
          this.validatedRevision = revision;
          this.message = { kind: "success", text: "Server validation passed for the current draft." };
        } catch (error) {
          this.validation = null;
          this.validatedRevision = null;
          this.handleActionError(error);
        } finally {
          this.busyAction = null;
        }
      },

      async previewDraft() {
        if (!this.canPreview) return;
        const revision = this.draftRevision;
        this.busyAction = "preview";
        try {
          const result = await apiRequest("/v1/admin/policies/preview", {
            method: "POST",
            authenticated: true,
            body: {
              policy: clone(this.draft),
              gateway_cases: clone(GATEWAY_CASES),
              priority_cases: clone(PRIORITY_CASES),
            },
          });
          if (revision !== this.draftRevision) return;
          this.preview = result.data;
          this.previewedRevision = revision;
          if (result.data.base_version !== this.baseVersion) {
            this.message = {
              kind: "error",
              text: "The active policy changed during preview. Reload the latest version before publishing.",
            };
          } else {
            this.message = { kind: "success", text: "Impact preview is current. Add a change note to publish." };
          }
        } catch (error) {
          this.preview = null;
          this.previewedRevision = null;
          this.handleActionError(error);
        } finally {
          this.busyAction = null;
        }
      },

      async publishDraft() {
        if (!this.canPublish) return;
        this.busyAction = "publish";
        try {
          const result = await apiRequest("/v1/admin/policies/publish", {
            method: "POST",
            authenticated: true,
            body: {
              base_version: this.baseVersion,
              change_note: this.changeNote.trim(),
              policy: clone(this.draft),
            },
          });
          const publishedVersion = result.data.version;
          await this.loadActive(true);
          await this.loadHistory();
          this.message = { kind: "success", text: `Policy v${publishedVersion} was published successfully.` };
        } catch (error) {
          this.handleActionError(error);
        } finally {
          this.busyAction = null;
        }
      },

      async compareVersion(version) {
        this.busyAction = "compare";
        try {
          const result = await apiRequest(`/v1/admin/policies/${version}`, { authenticated: true });
          this.comparison = {
            version: result.data.version,
            diff: diffObjects(result.data.policy, this.activePolicy.policy),
          };
          this.message = { kind: "info", text: `Comparing policy v${version} with active v${this.baseVersion}.` };
        } catch (error) {
          this.handleActionError(error);
        } finally {
          this.busyAction = null;
        }
      },

      closeComparison() {
        this.comparison = null;
      },

      prepareRollback(record) {
        this.rollbackTarget = clone(record);
        this.rollbackNote = "";
        this.comparison = null;
        this.message = { kind: "warning", text: `Review the rollback to v${record.version} and provide a change note.` };
      },

      cancelRollback() {
        this.rollbackTarget = null;
        this.rollbackNote = "";
      },

      async rollbackPolicy() {
        if (!this.canRollback) return;
        const targetVersion = this.rollbackTarget.version;
        this.busyAction = "rollback";
        try {
          const result = await apiRequest(`/v1/admin/policies/${targetVersion}/rollback`, {
            method: "POST",
            authenticated: true,
            body: { base_version: this.baseVersion, change_note: this.rollbackNote.trim() },
          });
          const publishedVersion = result.data.version;
          await this.loadActive(true);
          await this.loadHistory();
          this.rollbackTarget = null;
          this.rollbackNote = "";
          this.message = {
            kind: "success",
            text: `Policy v${targetVersion} was rolled forward as active v${publishedVersion}.`,
          };
        } catch (error) {
          this.handleActionError(error);
        } finally {
          this.busyAction = null;
        }
      },

      handleActionError(error) {
        const safeError = error instanceof ApiError ? error : new ApiError(0, "unknown", "An unexpected error occurred.");
        if (safeError.status === 401) {
          adminKey = "";
          this.connected = false;
          this.history = [];
          this.gateError = safeError.message;
        }
        if (safeError.status === 409) {
          this.validatedRevision = null;
          this.previewedRevision = null;
          this.validation = null;
          this.preview = null;
        }
        if (safeError.status === 422) this.serverErrors = safeError.details;
        this.message = { kind: "error", text: safeError.message };
      },

      formatValue(value) {
        if (value === null) return "null";
        if (value === undefined) return "—";
        if (typeof value === "object") return JSON.stringify(value);
        return String(value);
      },

      formatOrder(order) {
        return order.length ? order.join(" → ") : "No simulated jobs";
      },

      routingProvider(decision) {
        return decision && decision.provider ? decision.provider : "No provider selected";
      },

      routingReason(decision) {
        return decision && decision.reason ? decision.reason : "No routing reason returned.";
      },

      routingMetrics(decision) {
        if (!decision) return "No metrics returned";
        const metrics = [];
        if (decision.estimated_cost_usd !== undefined) metrics.push(`Cost $${decision.estimated_cost_usd}`);
        if (decision.typical_latency_ms !== undefined) metrics.push(`Latency ${decision.typical_latency_ms} ms`);
        return metrics.length ? metrics.join(" · ") : "No cost or latency estimate";
      },

      formatDate(value) {
        try {
          return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
        } catch (_) {
          return value;
        }
      },

      historyStatusClass(status) {
        return status === "active" ? "active" : "archived";
      },

      get activeStatusLabel() {
        return this.activePolicy ? `Active v${this.activePolicy.version}` : "Loading active policy";
      },

      get activeStatusClass() {
        return this.activePolicy ? "online" : "pending";
      },

      get publishedAtLabel() {
        return this.activePolicy ? `Published ${this.formatDate(this.activePolicy.published_at)}` : "";
      },

      get hasDraftChanges() {
        return Boolean(this.draft && this.activePolicy && diffObjects(this.activePolicy.policy, this.draft).length);
      },

      get revisionStatusLabel() {
        if (this.hasDraftChanges) return `Draft r${this.draftRevision} · unpublished`;
        return "Synced with active";
      },

      get revisionStatusClass() {
        return this.hasDraftChanges ? "dirty" : "clean";
      },

      get urgencyOrderError() {
        return localUrgencyOrderError(this.draft);
      },

      get validationMessages() {
        const messages = [];
        Object.entries(this.localFieldErrors).forEach(([path, text]) => messages.push({ key: `local-${path}`, text }));
        if (this.urgencyOrderError) messages.push({ key: "urgency-order", text: this.urgencyOrderError });
        this.serverErrors.forEach((error) => {
          const field = FIELD_BY_PATH[error.path];
          const prefix = field ? `${field.label}: ` : error.path ? `${error.path}: ` : "";
          const suffix = field ? ` Expected ${field.rangeLabel}.` : "";
          messages.push({ key: `server-${error.key}`, text: `${prefix}${error.message}${suffix}` });
        });
        if (this.validation && Array.isArray(this.validation.warnings)) {
          this.validation.warnings.forEach((warning, index) => {
            messages.push({ key: `warning-${index}`, text: `Warning: ${warning}` });
          });
        }
        return messages;
      },

      get canValidate() {
        return (
          this.connected &&
          this.draft !== null &&
          this.busyAction === null &&
          Object.keys(this.localFieldErrors).length === 0 &&
          !this.urgencyOrderError
        );
      },

      get canPreview() {
        return this.connected && canPreviewRevision(this);
      },

      get canPublish() {
        return this.connected && canPublishRevision(this);
      },

      get canRollback() {
        const length = this.rollbackNote.trim().length;
        return this.rollbackTarget !== null && this.busyAction === null && length >= 1 && length <= 500;
      },

      get changeNoteError() {
        const length = this.changeNote.trim().length;
        if (length === 0) return "A change note is required before publishing.";
        if (length > 500) return "Change note must be 500 characters or fewer.";
        return "";
      },

      get rollbackNoteError() {
        const length = this.rollbackNote.trim().length;
        if (length === 0) return "A rollback change note is required.";
        if (length > 500) return "Rollback note must be 500 characters or fewer.";
        return "";
      },

      get changeNoteCountLabel() {
        return `${this.changeNote.length}/500 characters`;
      },

      get validationStepClass() {
        return this.validatedRevision === this.draftRevision ? "complete" : "current";
      },

      get validationStepLabel() {
        return this.validatedRevision === this.draftRevision ? "Passed for this revision" : "Required for this revision";
      },

      get previewStepClass() {
        if (this.previewedRevision === this.draftRevision) return "complete";
        return this.validatedRevision === this.draftRevision ? "current" : "locked";
      },

      get previewStepLabel() {
        if (this.previewedRevision === this.draftRevision) return "Current impact captured";
        return this.validatedRevision === this.draftRevision ? "Ready to run" : "Validate first";
      },

      get publishStepClass() {
        return this.canPublish ? "current" : "locked";
      },

      get publishStepLabel() {
        if (this.canPublish) return "Ready to publish";
        if (this.previewedRevision !== this.draftRevision) return "Preview first";
        return "Add a change note";
      },

      get messageClass() {
        return this.message.kind;
      },
    };
  }

  globalThis.PolicyAdminLogic = Object.freeze({
    getAtPath,
    setAtPath,
    invalidateRevisions,
    canPreviewRevision,
    canPublishRevision,
    localUrgencyOrderError,
    mapValidationDetails,
    diffObjects,
  });

  document.addEventListener("alpine:init", () => {
    globalThis.Alpine.data("policyAdminApp", createPolicyAdminApp);
  });
})();
