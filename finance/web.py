"""The app: JSON API, static phone UI and the background sync scheduler.

Every response is built from the local database and plan.json. Access tokens live only in items.json,
which nothing here reads except the sync, so no response can carry one.
"""
import asyncio
import contextlib
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config, connections, db, lock, manual, money, plan, prices, store, sync, views
from .plaid import PlaidClient, PlaidError

log = logging.getLogger("finance")
STATIC = Path(__file__).parent / "static"
# Plaid Link is the one outside script: the Money screen loads it on demand to add or fix a bank, and it runs
# inside Plaid's own frame. Plaid already holds what the app shows, so this lets no data go anywhere new.
PLAID_LINK_JS = "https://cdn.plaid.com/link/v2/stable/link-initialize.js"
CSP = (f"default-src 'self'; img-src 'self' data:; script-src 'self' {PLAID_LINK_JS}; style-src 'self'; "
       "connect-src 'self'; frame-src https://cdn.plaid.com; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


def security_headers(app):
    """No outside scripts or framing on any page; API answers hold balances, so browsers must not cache them."""
    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        api = scope["path"].startswith("/api/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [*message.get("headers", ()), (b"content-security-policy", CSP.encode()),
                           (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
                if api:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, send_with_headers)
    return wrapped


def locked(app, the_lock):
    """Phone requests to the API get a 401 until they unlock; see lock.py. Pages and scripts stay open
    so the lock screen can load."""
    async def wrapped(scope, receive, send):
        if scope["type"] == "http" and await asyncio.to_thread(the_lock.blocks, scope["path"], Headers(scope=scope)):
            return await JSONResponse({"locked": True}, status_code=401)(scope, receive, send)
        await app(scope, receive, send)
    return wrapped


async def auth(request: Request):
    # Off the event loop: a password check runs scrypt, which takes a moment on purpose.
    status, body, cookie = await asyncio.to_thread(request.app.state.lock.handle, request.method, request.url.path,
                                                   request.headers, await request.body())
    response = JSONResponse(body, status_code=status)
    if cookie:
        response.headers["set-cookie"] = cookie
    return response


def _ctx(request: Request):
    with contextlib.closing(db.connect()) as conn:
        return views.gather(conn, plan.load(), request.app.state.clock(), manual.load())


def health(request: Request):
    with contextlib.closing(db.connect()) as conn:
        conn.execute("SELECT 1")
    return JSONResponse({"ok": True})


def screen(fn):
    def endpoint(request: Request):
        return JSONResponse(fn(_ctx(request)))
    return endpoint


MAX_RANGE_DAYS = 3 * 366


def _dates(q):
    """(start, end) dates from the query, or an error message."""
    try:
        start, end = date.fromisoformat(q["start"]), date.fromisoformat(q["end"])
    except (KeyError, ValueError):
        return None, "start and end must both be dates like 2026-10-01"
    if end < start or start.year < 2000 or (end - start).days > MAX_RANGE_DAYS:
        return None, "end must be on or after start, from 2000 on and at most 3 years apart"
    return (start, end), None


def _period(q, ctx):
    """(period, start, end, error) from ?period= or ?start=&end=. A custom range wins over a period."""
    if "start" in q or "end" in q:
        dates, error = _dates(q)
        return ("custom", *dates, None) if dates else (None, None, None, error)
    period = q.get("period", "month")
    if period not in views.PERIODS:
        return None, None, None, f"period must be one of {', '.join(views.PERIODS)}, or start and end"
    if period == "pay_period" and not ctx["plan"].get("income"):
        return None, None, None, "the pay period needs a pay schedule in the plan"
    return period, None, None, None


def spending(request: Request):
    ctx = _ctx(request)
    period, start, end, error = _period(request.query_params, ctx)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    return JSONResponse(views.spending(ctx, period, start, end))


def transactions(request: Request):
    q = request.query_params
    try:
        limit = min(int(q.get("limit", 200)), 2000)
    except ValueError:
        return JSONResponse({"error": "limit must be a number"}, status_code=400)
    ctx = _ctx(request)
    start, end, period = q.get("start"), q.get("end"), None
    if "period" in q or ("start" in q and "end" in q):
        period, first, last, error = _period(q, ctx)
        if error:
            return JSONResponse({"error": error}, status_code=400)
        if period == "custom":
            start, end, period = first.isoformat(), last.isoformat(), None
    return JSONResponse(views.transactions(ctx, start, end, q.get("account"), q.get("q"), q.get("category"),
                                           limit, period, q.get("merchant")))


def alerts(request: Request):
    return JSONResponse(views.alerts(_ctx(request)))


async def dismiss_alert(request: Request):
    """Hide one alert. An id no alert has is dropped: it already went away while the page was open."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    alert_id = body.get("id") if isinstance(body, dict) else None
    if not isinstance(alert_id, str):
        return JSONResponse({"error": "body must be {\"id\": \"<alert id>\"}"}, status_code=400)
    return JSONResponse(await asyncio.to_thread(_dismiss, alert_id, request.app.state.clock()))


def _dismiss(alert_id, now):
    with contextlib.closing(db.connect()) as conn:
        ctx = views.gather(conn, plan.load(), now, manual.load())
        if any(a["id"] == alert_id for a in views.alerts(ctx)["alerts"]):
            store.dismiss_alert(conn, alert_id, ctx["now"].isoformat(timespec="seconds"))
            ctx["dismissed_alerts"] = store.dismissed_alerts(conn)
    return views.alerts(ctx)


def restore_alerts(request: Request):
    with contextlib.closing(db.connect()) as conn:
        store.restore_alerts(conn)
    return JSONResponse(views.alerts(_ctx(request)))


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


def get_manual(request: Request):
    return JSONResponse(manual.load())


async def put_manual(request: Request):
    """Save manual.json, then price it at once so a mistyped symbol shows up in the answer."""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"errors": ["body must be JSON"]}, status_code=400)
    try:
        saved = manual.save(body)
    except manual.ManualError as e:
        return JSONResponse({"errors": e.errors}, status_code=400)
    with contextlib.closing(db.connect()) as conn:
        fetched = await asyncio.to_thread(_refresh_prices, conn, request.app)
    return JSONResponse({"manual": saved, "prices": fetched})


# --- sync

def _refresh_prices(conn, app, brokerage=False):
    """Prices for the hand-entered holdings, and with `brokerage` for the linked accounts' tickers too."""
    tickers = sorted({h["ticker"] for h in store.holdings(conn) if h["ticker"] and ":" not in h["ticker"]
                      and money.bucket_of(h) != "cash"}) if brokerage else ()
    return prices.refresh(conn, manual.load(), app.state.clock().astimezone().date(), app.state.get_url, tickers)


def _sync_blocking(app):
    with contextlib.closing(db.connect()) as conn:
        results = sync.sync_all(conn, app.state.make_client)
        try:
            _refresh_prices(conn, app, brokerage=True)
        except Exception:  # prices are a side dish: never fail the bank sync over them
            log.exception("price refresh failed")
        return results


async def run_sync(app):
    async with app.state.sync_lock:
        results = await asyncio.to_thread(_sync_blocking, app)
    for r in results:
        log.info("sync %s: %s%s", r["item_id"], r["status"], f" ({r['error_code']})" if r.get("error_code") else "")
    return results


def _last_attempt():
    with contextlib.closing(db.connect()) as conn:
        times = [it["last_attempt_at"] for it in store.items(conn) if it.get("last_attempt_at")]
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
    results = [{"id": connections.connection_id(r["item_id"]), **{k: v for k, v in r.items() if k != "item_id"}}
               for r in await run_sync(app)]
    return JSONResponse({"status": "synced", "results": results})


# --- connections (banks linked through Plaid)

async def _body(request):
    """The JSON object a write sent. JSON only, so another site's page can't send one without a CORS preflight."""
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise ValueError("send JSON (Content-Type: application/json)")
    try:
        body = await request.json()
    except ValueError:
        raise ValueError("body must be JSON") from None
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    return body


def _plaid_errors(fn):
    """Plaid's code and message only, never the request; an unknown connection is a 404."""
    async def endpoint(request: Request):
        try:
            return await fn(request)
        except PlaidError as e:
            return JSONResponse({"error": f"{e.code}: {e.message}"}, status_code=502)
        except LookupError:
            return JSONResponse({"error": "no such connection"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
    return endpoint


def _in_db(fn, *args):
    with contextlib.closing(db.connect()) as conn:
        return fn(conn, *args)


@_plaid_errors
async def link_token(request: Request):
    body = await _body(request)
    token = await asyncio.to_thread(_in_db, connections.link_token, request.app.state.make_client,
                                    body.get("kind"), body.get("id"))
    return JSONResponse({"link_token": token})


async def _sync_after_link(app, item_id, name, done):
    """Sync so the bank shows up at once, and say how that went for this bank."""
    result = next((r for r in await run_sync(app) if r["item_id"] == item_id), {"status": "error"})
    if result["status"] == "ok":
        return {"message": f"{done} {name}.", "status": "ok"}
    if result["status"] == "not_ready":
        return {"message": f"{done} {name}. Plaid is still gathering its data; it shows up within a few hours.",
                "status": "not_ready"}
    return {"message": f"{done} {name}, but the first sync failed ({result.get('error_code') or 'error'}). "
                       "It retries on its own.", "status": result["status"]}


@_plaid_errors
async def add_connection(request: Request):
    body = await _body(request)
    public_token, name = body.get("public_token"), body.get("institution")
    if not isinstance(public_token, str) or not public_token:
        raise ValueError("public_token is required")
    name = name.strip()[:80] if isinstance(name, str) and name.strip() else "Unnamed bank"
    async with request.app.state.sync_lock:  # a removal in progress rewrites items.json too
        item_id = await asyncio.to_thread(connections.add, request.app.state.make_client, public_token, name)
    return JSONResponse(await _sync_after_link(request.app, item_id, name, "Linked"))


@_plaid_errors
async def connection_renewed(request: Request):
    await _body(request)
    item_id = await asyncio.to_thread(_in_db, connections.renewed, request.path_params["cid"])
    name = next((it["institution"] for it in await asyncio.to_thread(_in_db, store.items)
                 if it["item_id"] == item_id), None) or "the bank"
    return JSONResponse(await _sync_after_link(request.app, item_id, name, "Updated"))


@_plaid_errors
async def remove_connection(request: Request):
    async with request.app.state.sync_lock:  # a sync running now would write the bank's data back
        name = await asyncio.to_thread(_in_db, connections.remove, request.app.state.make_client,
                                       request.path_params["cid"])
    return JSONResponse({"message": f"Removed {name or 'the bank'} and everything stored from it."})


async def scheduler(app):
    """Sync every SYNC_INTERVAL_HOURS; retry sooner while Plaid is still preparing an Item."""
    ran_at, not_ready = None, False
    while True:
        last = max((t for t in (_last_attempt(), ran_at) if t), default=None)  # manual syncs count too
        interval = (timedelta(minutes=config.NOT_READY_RETRY_MINUTES) if not_ready
                    else timedelta(hours=config.SYNC_INTERVAL_HOURS))
        wait = (last + interval - app.state.clock()).total_seconds() if last else 0
        if wait > 0:
            await asyncio.sleep(min(wait, 600))
            continue
        ran_at = app.state.clock()
        try:
            results = await run_sync(app)
        except Exception:  # keep the scheduler alive; the error type is enough to diagnose
            log.exception("scheduled sync failed")
            results = []
        not_ready = any(r["status"] == "not_ready" for r in results)


def create_app(clock=None, make_client=None, get_url=None, lock_clock=None) -> Starlette:
    the_lock = lock.Lock(config.lock_path(), cookie="money_lock", clock=lock_clock or time.time, rp_name="Money")

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
            Route("/api/auth", auth, methods=["GET", "POST"]),
            Route("/api/auth/{action:path}", auth, methods=["GET", "POST"]),
            Route("/api/status", screen(views.status)),
            Route("/api/today", screen(views.today)),
            Route("/api/spending", spending),
            Route("/api/bills", screen(views.bills)),
            Route("/api/accounts", screen(views.accounts)),
            Route("/api/holdings", screen(views.holdings)),
            Route("/api/transactions", transactions),
            Route("/api/alerts", alerts),
            Route("/api/alerts/dismissed", dismiss_alert, methods=["PUT"]),
            Route("/api/alerts/dismissed", restore_alerts, methods=["DELETE"]),
            Route("/api/snapshot", snapshot),
            Route("/api/plan", get_plan, methods=["GET"]),
            Route("/api/plan", put_plan, methods=["PUT"]),
            Route("/api/manual", get_manual, methods=["GET"]),
            Route("/api/manual", put_manual, methods=["PUT"]),
            Route("/api/sync", sync_now, methods=["POST"]),
            Route("/api/connections", add_connection, methods=["POST"]),
            Route("/api/connections/link_token", link_token, methods=["POST"]),
            Route("/api/connections/{cid}/renewed", connection_renewed, methods=["POST"]),
            Route("/api/connections/{cid}", remove_connection, methods=["DELETE"]),
            Mount("/", StaticFiles(directory=STATIC, html=True, check_dir=False)),
        ],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts()), Middleware(security_headers),
                    Middleware(locked, the_lock=the_lock)],
        lifespan=lifespan,
    )
    app.state.lock = the_lock
    app.state.clock = clock or (lambda: datetime.now(timezone.utc))
    app.state.make_client = make_client or PlaidClient
    app.state.get_url = get_url or prices.http_get
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
