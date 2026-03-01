import json
from functools import lru_cache

from openai import OpenAI
from pydantic import BaseModel

from src.observability.tracer import get_tracer
from src.utils.config import get_settings


@lru_cache(maxsize=1)
def get_llm_client() -> OpenAI:
    settings = get_settings()

    return OpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )


def chat(messages: list, model: str = None) -> str:
    settings = get_settings()
    client = get_llm_client()

    tracer = None
    trace = None

    try:
        tracer = get_tracer()
        trace = tracer.trace(
            name="llm_chat",
            metadata={"model": model or settings.primary_model},
        )
    except Exception:
        pass

    response = client.chat.completions.create(
        model=model or settings.primary_model,
        messages=messages,
        max_tokens=2048,
    )

    output = response.choices[0].message.content

    if trace:
        try:
            trace.update(output=output)
        except Exception:
            pass

    return output


def structured_chat(messages: list, schema: type[BaseModel], model: str = None):
    """
    Structured chat with defensive parsing + retries.
    """

    schema_json = json.dumps(schema.model_json_schema(), indent=2)

    messages = messages + [
        {
            "role": "system",
            "content": (
                "Return ONLY valid JSON. "
                "Do NOT include markdown. "
                "Do NOT return the schema itself. "
                "Return an INSTANCE matching the schema."
            ),
        }
    ]

    messages[-1]["content"] += f"\n\nJSON schema:\n{schema_json}"

    # --- retry loop ---
    for attempt in range(3):
        raw = chat(messages, model)

        if not raw:
            continue

        clean = raw.strip()

        if "```" in clean:
            clean = clean.replace("```json", "").replace("```", "").strip()

        start = clean.find("{")
        end = clean.rfind("}") + 1

        if start == -1 or end <= start:
            continue

        clean = clean[start:end]

        try:
            return schema.model_validate_json(clean)
        except Exception:
            continue

    raise ValueError("LLM failed to produce valid structured JSON after retries")
