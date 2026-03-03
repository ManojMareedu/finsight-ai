
import json
import logging
from functools import lru_cache
from typing import Optional

from openai import OpenAI

from src.utils.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_llm_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )


def _normalize_messages(messages: list) -> list:
    """
    Merge all system messages into the first user message.

    Some free models on OpenRouter (Gemma, Phi, etc.) reject the
    'system' role entirely and return a 400 error. By folding system
    content into the user turn we stay compatible with every model
    OpenRouter might pick when using openrouter/auto or openrouter/free.
    """
    system_parts: list[str] = []
    other_messages: list[dict] = []

    for msg in messages:
        if msg.get("role") == "system":
            system_parts.append(msg["content"])
        else:
            other_messages.append(dict(msg))  # copy so we don't mutate caller's list

    if not system_parts:
        return other_messages

    system_block = "\n\n".join(system_parts)

    # Find the first user message and prepend the system block to it
    for msg in other_messages:
        if msg.get("role") == "user":
            msg["content"] = f"{system_block}\n\n{msg['content']}"
            return other_messages

    # No user message found — add one carrying the system content
    other_messages.insert(0, {"role": "user", "content": system_block})
    return other_messages


def chat(messages: list, model: Optional[str] = None) -> str:
    settings = get_settings()
    client = get_llm_client()
    model_name = model or settings.primary_model

    normalized = _normalize_messages(messages)

    # Langfuse tracing — optional, degrades gracefully if keys not set
    tracer = None
    trace = None
    try:
        from src.observability.tracer import get_tracer
        tracer = get_tracer()
        trace = tracer.trace(
            name="llm_chat",
            metadata={"model": model_name},
        )
    except Exception:
        pass

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=normalized,
            max_tokens=2048,
        )
        output = response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"LLM call failed (model={model_name}): {e}")
        raise

    if trace:
        try:
            trace.update(output=output)
            trace.end()
        except Exception:
            pass

    return output


def structured_chat(messages: list, schema, model: Optional[str] = None):
    """
    Chat with JSON schema enforcement and retry logic.

    Appends schema instructions to the last user message and retries
    up to 3 times on parse failure.
    """
    schema_json = json.dumps(schema.model_json_schema(), indent=2)

    # Copy messages and append schema instruction to the last message
    augmented = [dict(m) for m in messages]
    augmented[-1]["content"] = (
        augmented[-1]["content"]
        + f"\n\nReturn ONLY valid JSON matching this schema. "
        f"No markdown. No explanation. JSON only.\n\n{schema_json}"
    )

    for attempt in range(3):
        try:
            raw = chat(augmented, model)
        except Exception as e:
            logger.warning(f"structured_chat attempt {attempt + 1} failed at LLM call: {e}")
            continue

        if not raw:
            continue

        # Strip markdown fences if the model added them despite instructions
        clean = raw.strip()
        if "```" in clean:
            clean = clean.replace("```json", "").replace("```", "").strip()

        # Extract the outermost JSON object
        start = clean.find("{")
        end = clean.rfind("}") + 1
        if start == -1 or end <= start:
            logger.warning(f"structured_chat attempt {attempt + 1}: no JSON object found")
            continue

        clean = clean[start:end]

        try:
            return schema.model_validate_json(clean)
        except Exception as e:
            logger.warning(f"structured_chat attempt {attempt + 1} validation failed: {e}")
            continue

    raise ValueError(
        f"LLM failed to return valid JSON matching {schema.__name__} after 3 attempts. "
        f"Check that PRIMARY_MODEL is set correctly in .env"
    )