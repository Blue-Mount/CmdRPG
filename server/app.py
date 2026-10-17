"""Optional FastAPI adapter. Use local Repository without installing FastAPI."""

from contextlib import asynccontextmanager
import hmac
import os
import threading
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from server.storage import Repository, RepositoryError
from server.client_updates import read_channel, router as client_update_router
from runtime.updater import UpdateError


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRunBody(RequestBody):
    name: str = Field(default="远征队", min_length=1, max_length=40)
    templates: list[str] | None = Field(default=None, min_length=4, max_length=4)
    seed: StrictInt | None = None
    command_id: str = Field(min_length=1, max_length=128)


class PreviewBody(RequestBody):
    command: dict[str, Any]


class WriteBody(RequestBody):
    command_id: str = Field(min_length=1, max_length=128)
    expected_revision: Annotated[StrictInt, Field(ge=0)]


class CommandBody(WriteBody):
    command: dict[str, Any]


class ReactionBody(WriteBody):
    choice: Literal["accept", "decline"]


def create_app(database_path=None, token=None, *, repository: Repository | None = None,
               release_directory=None, minimum_client=None) -> FastAPI:
    """Create an API with independent configuration and fail-closed game access.

    An omitted token reads CMDRPG_TOKEN; an explicit empty token disables access.
    Storage is loaded on the first authenticated game request. Health stays
    available even when credentials or content have not been configured yet.
    """
    configured_token = os.environ.get("CMDRPG_TOKEN") if token is None else token
    database = database_path or os.environ.get("CMDRPG_DB", "data/game.sqlite3")
    repository_lock = threading.Lock()
    releases = release_directory or os.environ.get('CMDRPG_CLIENT_RELEASES')
    minimum = int(os.environ.get('CMDRPG_MIN_CLIENT_VERSION', '0') if minimum_client is None else minimum_client)
    if minimum < 0:
        raise ValueError('Minimum client version must be nonnegative')

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if app.state.repository is not None:
            app.state.repository.close()

    app = FastAPI(title="CmdRPG", version="0.3.0", lifespan=lifespan)
    app.state.repository = repository
    if releases:
        app.include_router(client_update_router(releases))

    @app.exception_handler(RepositoryError)
    async def repository_error(_request: Request, exc: RepositoryError):
        error = {"code": exc.code, "message": exc.message}
        if exc.revision is not None:
            error["current_revision"] = exc.revision
        return JSONResponse(status_code=exc.status_code, content={"error": error})

    def authenticated(authorization: Annotated[str | None, Header()] = None,
                      x_cmdrpg_client_version: Annotated[str | None, Header()] = None) -> Repository:
        if not configured_token:
            raise HTTPException(503, detail={"code": "token_not_configured",
                                            "message": "服务尚未配置 CMDRPG_TOKEN。"})
        scheme, separator, presented = (authorization or "").partition(" ")
        if (not separator or scheme.lower() != "bearer" or not presented
                or not hmac.compare_digest(presented.encode("utf-8"), configured_token.encode("utf-8"))):
            raise HTTPException(401, detail={"code": "unauthorized", "message": "需要有效的 Bearer 凭据。"},
                                headers={"WWW-Authenticate": "Bearer"})
        required = minimum
        if releases:
            try:
                required = max(required, read_channel(releases)['minimum'])
            except (OSError, ValueError, UpdateError):
                raise HTTPException(503, detail={'code': 'release_unavailable',
                                                'message': '客户端发布信息不可用，请稍后重试。'})
        try:
            supplied = int(x_cmdrpg_client_version or '0')
        except ValueError:
            supplied = 0
        if supplied < required:
            raise HTTPException(426, detail={'code': 'client_upgrade_required',
                'message': '服务器要求客户端版本 %s；请关闭游戏并升级后再连接。' % required})
        with repository_lock:
            if app.state.repository is None:
                app.state.repository = Repository(database)
        return app.state.repository

    Store = Annotated[Repository, Depends(authenticated)]

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/runs")
    def list_runs(store: Store):
        return store.list_runs()

    @app.post("/runs")
    def create_run(body: CreateRunBody, store: Store):
        return store.create_run(body.name, body.templates, body.seed, body.command_id)

    @app.get("/runs/{run_id}/view")
    def view(run_id: str, store: Store):
        return store.get_view(run_id)

    @app.post("/runs/{run_id}/preview")
    def preview(run_id: str, body: PreviewBody, store: Store):
        return store.preview(run_id, body.command)

    @app.post("/runs/{run_id}/options")
    def options(run_id: str, body: PreviewBody, store: Store):
        return store.options(run_id, body.command)

    @app.post("/runs/{run_id}/commands")
    def command(run_id: str, body: CommandBody, store: Store):
        return store.submit(run_id, body.command_id, body.expected_revision, body.command)

    @app.post("/runs/{run_id}/reactions")
    def react(run_id: str, body: ReactionBody, store: Store):
        return store.react(run_id, body.command_id, body.expected_revision, body.choice)

    @app.post("/runs/{run_id}/retry")
    def retry(run_id: str, body: WriteBody, store: Store):
        return store.retry(run_id, body.command_id, body.expected_revision)

    @app.get("/runs/{run_id}/events")
    def events(run_id: str, store: Store, after: int = Query(default=0, ge=0),
               limit: int = Query(default=20, ge=1, le=200)):
        return store.events(run_id, after, limit)

    return app


app = create_app()
