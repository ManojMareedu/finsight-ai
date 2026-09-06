import logging
import os
import sys
import uuid

from fastapi import FastAPI, Request

from src.api.routes.analyze import router as analyze_router
from src.api.routes.health import router as health_router
from src.observability.logging import configure_logging, request_id_var
from src.rag.embeddings import get_embeddings
from src.utils.config import get_settings

configure_logging(get_settings().log_level)

logger = logging.getLogger(__name__)


def _check_single_worker() -> None:
    # T2-follow-up (item 32): the job store, response cache, and rate limiter
    # in src/api/routes/analyze.py are plain in-process dicts, correct only
    # under exactly one worker. start.sh is H's file this tier and hardcodes
    # --workers 1; this just makes a future drift off that loud instead of a
    # silent multi-worker landmine (invisible jobs, per-worker cache misses,
    # a rate limit that's effectively N times looser).
    workers = os.environ.get("WEB_CONCURRENCY", "")
    argv = sys.argv
    if "--workers" in argv:
        i = argv.index("--workers")
        if i + 1 < len(argv):
            workers = argv[i + 1]
    if workers.isdigit() and int(workers) > 1:
        logger.critical(
            "FINSIGHT: starting with %s uvicorn workers, but the job store, "
            "response cache, and rate limiter are per-worker state — jobs "
            "submitted on one worker 404 when polled on another, cache hits "
            "become per-worker (repeat LLM cost), and the rate limit is "
            "effectively %sx looser. Run --workers 1, or move that state to "
            "a shared store first.",
            workers,
            workers,
        )


_check_single_worker()

app = FastAPI(title="FinSight AI", version="1.0.0")


@app.on_event("startup")
async def _warm_embeddings() -> None:
    # T0-1: get_embeddings() lazily constructs a SentenceTransformer on first
    # call. Left alone, that first call happens inside a /health request —
    # a model load racing the Dockerfile's 10s HEALTHCHECK timeout on a cold
    # container. Warm it here so it's already cached before uvicorn accepts
    # traffic.
    get_embeddings()


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["x-request-id"] = request_id
    return response


app.include_router(health_router)
app.include_router(analyze_router)
