import logging
from typing import Any

logger = logging.getLogger("llm.usage")


def record_llm_call(model: str, usage: Any) -> None:
    """Log one model call with its token counts.

    OpenRouter omits the usage block on some free models, so the counts are
    optional and the call itself is what is always recorded — a call count is
    still enough to see which company or agent is burning the daily quota.
    """
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    logger.info(
        "llm_call model=%s prompt_tokens=%s completion_tokens=%s",
        model,
        "unknown" if prompt is None else prompt,
        "unknown" if completion is None else completion,
    )
