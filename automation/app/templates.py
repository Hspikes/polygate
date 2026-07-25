from __future__ import annotations

from automation.app.models import Preferences, Scenario, TemplateDefinition


TEMPLATES: tuple[TemplateDefinition, ...] = (
    TemplateDefinition(
        id=Scenario.production_incident,
        label="Production Incident",
        defaults=Preferences(quality="high", privacy="high", max_cost_usd=0.01, latency_target_ms=1000),
        locked_fields=[],
        scenario_weight=40,
    ),
    TemplateDefinition(
        id=Scenario.customer_escalation,
        label="Customer Escalation",
        defaults=Preferences(quality="balanced", privacy="standard", max_cost_usd=0.01, latency_target_ms=1500),
        locked_fields=[],
        scenario_weight=25,
    ),
    TemplateDefinition(
        id=Scenario.finance_summary,
        label="Finance Document Summary",
        defaults=Preferences(quality="balanced", privacy="high", max_cost_usd=0.005, latency_target_ms=3000),
        locked_fields=["privacy"],
        scenario_weight=15,
    ),
    TemplateDefinition(
        id=Scenario.marketing_batch,
        label="Marketing Batch Content",
        defaults=Preferences(quality="cheap", privacy="standard", max_cost_usd=0.002, latency_target_ms=5000),
        locked_fields=[],
        scenario_weight=0,
    ),
)
