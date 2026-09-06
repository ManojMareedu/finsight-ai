import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.utils.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _check_core() -> None:
    """
    Liveness checks: the Chroma persistence dir opens and the local embedding
    model loads. Both are local/in-process — no network calls, so this can't
    hang on a flaky connection and never makes a billable call.
    """
    import chromadb

    from src.rag.embeddings import get_embeddings

    settings = get_settings()
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    client.get_or_create_collection("financial_filings")
    get_embeddings()  # cached after first call — cheap on every poll after that


@router.get("/health")
def health():
    """Liveness probe used by the Docker HEALTHCHECK and start.sh — verifies
    Chroma and the embedding model actually work, not just that the process
    is up."""
    try:
        _check_core()
    except Exception as e:
        logger.warning(f"Health check failed: {e}")
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})
    return {"status": "ok"}


@router.get("/ready")
def ready():
    """
    Readiness probe: liveness checks plus LLM provider configuration.
    $0 rule: checks key presence only — never calls the LLM, so this can
    never incur a billable request.
    """
    try:
        _check_core()
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
    except Exception as e:
        logger.warning(f"Readiness check failed: {e}")
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})
    return {"status": "ok"}
