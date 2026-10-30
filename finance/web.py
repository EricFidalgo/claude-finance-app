"""The app: JSON API, static phone UI and the background sync scheduler."""
import contextlib

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import db, plan


def health(request: Request):
    request.app.state.conn.execute("SELECT 1")
    return JSONResponse({"ok": True})


def get_plan(request: Request):
    return JSONResponse(plan.load())


async def put_plan(request: Request):
    return JSONResponse(plan.save(await request.json()))


def create_app() -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app):
        app.state.conn = db.connect()
        yield
        app.state.conn.close()

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/api/plan", get_plan, methods=["GET"]),
            Route("/api/plan", put_plan, methods=["PUT"]),
        ],
        lifespan=lifespan,
    )


app = create_app()
