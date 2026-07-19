"""PolyGate Monitoring API: stable JSON views over fixed Prometheus queries."""

import os

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.models import HealthResponse, MonitoringOverview, Window
from app.prometheus import PrometheusClient, PrometheusError
from app.service import build_overview


app = FastAPI(title="PolyGate Monitoring API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "CORS_ALLOW_ORIGINS",
            "http://localhost:8080",
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.state.prometheus = PrometheusClient(
    base_url=os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
    timeout_seconds=float(
        os.environ.get("PROMETHEUS_TIMEOUT_SECONDS", "3")
    ),
)


def get_prometheus() -> PrometheusClient:
    return app.state.prometheus


@app.get("/health", response_model=HealthResponse)
def health(
    prometheus: PrometheusClient = Depends(get_prometheus),
):
    reachable = prometheus.ready()
    response = HealthResponse(
        status="ok" if reachable else "degraded",
        prometheus_reachable=reachable,
    )
    if not reachable:
        return JSONResponse(
            status_code=503,
            content=response.model_dump(),
        )
    return response


@app.get(
    "/api/monitoring/overview",
    response_model=MonitoringOverview,
)
def overview(
    window: Window = Query(default="15m"),
    prometheus: PrometheusClient = Depends(get_prometheus),
) -> MonitoringOverview:
    try:
        return build_overview(prometheus, window)
    except PrometheusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
