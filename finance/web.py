"""The app: JSON API, static phone UI and the background sync scheduler.

Every response is built from the local database and data/plan.json. Access tokens live only in data/items.json,
which nothing here reads except the sync, so no response can carry one.
"""
import asyncio
import contextlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config, db, plan, sync, views

log = logging.getLogger("finance")
STATIC = Path(__file__).parent / "static"


def _ctx(request: Request):
    with contextlib.closing(db.connect()) as conn:
        return views.gather(conn, plan.load(), request.app.state.clock())


def health(request: Request):
    with contextlib.closing(db.connect()) as conn:
        conn.execute("SELECT 1")
    return JSONResponse({"ok": True})


def screen(fn):
    def endpoint(request: Request):
        return JSONResponse(fn(_ctx(request)))
    return endpoint


def spending(request: Request):
    period = request.query_params.get("period", "month")
    if period not in views.PERIODS:
        return JSONResponse({"error": f"period must be one of {', '.join(views.PERIODS)}"}, status_code=400)
    return JSONResponse(views.spending(_ctx(request), period))


def transactions(request: Request):
    q = request.query_params
    try:
        limit = min(int(q.get("limit", 200)), 2000)
    except ValueError:
        return JSONResponse({"error": "limit must be a number"}, status_code=400)
    return JSONResponse(views.transactions(_ctx(request), q.get("start"), q.get("end"), q.get("account"),
                                           q.get("q"), q.get("category"), limit))


def alerts(request: Request):
    return JSONResponse({"alerts": views.alerts(_ctx(request))})


def snapshot(request: Request):
    return PlainTextResponse(views.snapshot(_ctx(request), request.query_params.get("screen")))


def get_plan(request: Request):
    return JSONResponse(plan.load())


async def put_plan(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"errors": ["body must be JSON"]}, status_code=400)
    try:
        return JSONResponse(plan.save(body))
    except plan.PlanError as e:
        return JSONResponse({"errors": e.errors}, status_code=400)


# --- sync

def _sync_blocking(make_client):
    with contextlib.closing(db.connect()) as conn:
        return sync.sync_all(conn, make_client)


async def run_sync(app):
    async with app.state.sync_lock:
        results = await asyncio.to_thread(_sync_blocking, app.state.make_client)
    for r in results:
        log.info("sync %s: %s%s", r["item_id"], r["status"], f" ({r['error_code']})" if r.get("error_code") else "")
    return results


def _last_attempt():
    with contextlib.closing(db.connect()) as conn:
        times = [it["last_attempt_at"] for it in views.store.items(conn) if it.get("last_attempt_at")]
    return datetime.fromisoformat(max(times)) if times else None


async def sync_now(request: Request):
    """Manual sync. Ignored within a few minutes of the last attempt unless {"force": true}."""
    try:
        force = bool((await request.json()).get("force"))
    except ValueError:
        force = False
    app = request.app
    if app.state.sync_lock.locked():
        return JSONResponse({"status": "running"})
    last = _last_attempt()
    if not force and last and app.state.clock() - last < timedelta(minutes=config.MANUAL_SYNC_MIN_MINUTES):
        return JSONResponse({"status": "skipped", "last_attempt_at": last.isoformat()})
    return JSONResponse({"status": "synced", "results": await run_sync(app)})


async def scheduler(app):
    """Sync every SYNC_INTERVAL_HOURS; retry sooner while Plaid is still preparing an Item."""
    while True:
        last = _last_attempt()
        due = last + timedelta(hours=config.SYNC_INTERVAL_HOURS) if last else datetime.now(timezone.utc)
        wait = (due - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            await asyncio.sleep(min(wait, 600))
            continue
        try:
            results = await run_sync(app)
        except Exception:  # keep the scheduler alive; the error type is enough to diagnose
            log.exception("scheduled sync failed")
            results = []
        if any(r["status"] == "not_ready" for r in results):
            await asyncio.sleep(config.NOT_READY_RETRY_MINUTES * 60)
            await run_sync(app)


def create_app(clock=None, make_client=None) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app):
        os.umask(0o077)  # database and plan files are readable by the owner only
        db.connect().close()
        plan.ensure_file()
        app.state.sync_lock = asyncio.Lock()
        task = asyncio.create_task(scheduler(app)) if config.scheduler_enabled() else None
        yield
        if task:
            task.cancel()

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/api/status", screen(views.status)),
            Route("/api/today", screen(views.today)),
            Route("/api/spending", spending),
            Route("/api/bills", screen(views.bills)),
            Route("/api/accounts", screen(views.accounts)),
            Route("/api/holdings", screen(views.holdings)),
            Route("/api/transactions", transactions),
            Route("/api/alerts", alerts),
            Route("/api/snapshot", snapshot),
            Route("/api/plan", get_plan, methods=["GET"]),
            Route("/api/plan", put_plan, methods=["PUT"]),
            Route("/api/sync", sync_now, methods=["POST"]),
            Mount("/", StaticFiles(directory=STATIC, html=True, check_dir=False)),
        ],
        lifespan=lifespan,
    )
    app.state.clock = clock or (lambda: datetime.now(timezone.utc))
    app.state.make_client = make_client
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
