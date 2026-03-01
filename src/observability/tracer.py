from langfuse import Langfuse
from functools import lru_cache
from src.utils.config import get_settings


@lru_cache(maxsize=1)
def get_tracer():
    settings = get_settings()

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )