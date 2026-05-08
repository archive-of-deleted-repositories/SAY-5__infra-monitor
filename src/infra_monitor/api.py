"""FastAPI dashboard.

Endpoints
---------
* ``GET /`` — Jinja2 dashboard (host list, trend charts, recent alerts/remediations)
* ``GET /api/healthz``
* ``GET /api/hosts``
* ``GET /api/metrics?host_id=&metric=&since=``
* ``GET /api/alerts?limit=``
* ``GET /api/remediations?limit=``
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from infra_monitor.settings import Settings, get_settings
from infra_monitor.store import Storage

PACKAGE_ROOT = Path(__file__).parent
TEMPLATES_DIR = PACKAGE_ROOT / "dashboard" / "templates"
STATIC_DIR = PACKAGE_ROOT / "dashboard" / "static"


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def create_app(*, storage: Storage | None = None, settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    store = storage or Storage.from_url(cfg.database_url)

    app = FastAPI(title="infra-monitor", version="0.1.0")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/api/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/hosts")
    def list_hosts() -> list[dict[str, object]]:
        return [
            {
                "id": h.id,
                "name": h.name,
                "kind": h.kind,
                "cloud_id": h.cloud_id,
                "created_at": _to_iso(h.created_at),
            }
            for h in store.list_hosts()
        ]

    @app.get("/api/metrics")
    def metrics(
        host_id: int = Query(...),
        metric: str = Query(...),
        minutes: int = Query(60, ge=1, le=60 * 24 * 7),
    ) -> dict[str, object]:
        since = datetime.now(UTC) - timedelta(minutes=minutes)
        rows = store.metrics(host_id=host_id, metric=metric, since=since)
        return {
            "host_id": host_id,
            "metric": metric,
            "points": [{"t": _to_iso(r.recorded_at), "v": r.value} for r in rows],
        }

    @app.get("/api/alerts")
    def alerts(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, object]]:
        rows = store.recent_alerts(limit=limit)
        return [
            {
                "id": r.id,
                "rule_id": r.rule_id,
                "host_id": r.host_id,
                "value": r.metric_value,
                "severity": r.severity,
                "fired_at": _to_iso(r.fired_at),
                "cleared_at": _to_iso(r.cleared_at) if r.cleared_at else None,
            }
            for r in rows
        ]

    @app.get("/api/remediations")
    def remediations(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, object]]:
        rows = store.recent_remediations(limit=limit)
        return [
            {
                "id": r.id,
                "rule_id": r.rule_id,
                "host_id": r.host_id,
                "script": r.script_path,
                "args": r.args_json,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
                "fired_at": _to_iso(r.fired_at),
                "stdout_excerpt": r.stdout_excerpt,
                "stderr_excerpt": r.stderr_excerpt,
            }
            for r in rows
        ]

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        hosts = store.list_hosts()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "hosts": [{"id": h.id, "name": h.name, "kind": h.kind} for h in hosts],
            },
        )

    @app.exception_handler(HTTPException)
    def _on_http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    return app
