from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralized project configuration.
    Automatically reads .env
    """

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    # API Keys
    openrouter_api_key: str
    tavily_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Model — a concrete free OpenRouter slug so the default stays $0 (not
    # "openrouter/auto", which can route to paid models). Matches .env.example.
    primary_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Storage
    chroma_persist_dir: str = "./data/chroma"

    # Behaviour
    log_level: str = "INFO"
    max_agent_iterations: int = 3
    risk_threshold: float = 0.7

    # Evaluation (RAGAS). Judge backend is configurable: "openrouter" (default —
    # works without a local daemon) or "ollama" (local). Eval runs manually/
    # locally, not in GH CI. Thresholds gate a run's pass/fail.
    #
    # The judge must reliably emit RAGAS's structured JSON. "openrouter/free" and
    # small models do not (RAGAS then returns NaN), so the judge has its own model
    # setting, independent of PRIMARY_MODEL, defaulting to a capable free model.
    ragas_judge_provider: str = "openrouter"
    ragas_judge_model: str = "openai/gpt-oss-20b:free"
    ragas_ollama_model: str = "llama3.2"
    # Ollama's OpenAI-compatible endpoint. RAGAS parses the ChatOpenAI path but not
    # the native ChatOllama path, so the local judge talks to /v1.
    ollama_base_url: str = "http://localhost:11434/v1"
    ragas_faithfulness_min: float = 0.70
    ragas_answer_relevancy_min: float = 0.65
    # Cap eval samples (0 = all). Keeps a run under free-tier daily request caps.
    ragas_max_samples: int = 0
    # RAGAS RunConfig: per-call timeout (s) and worker concurrency. A high timeout
    # lets slower local judges finish instead of NaN-ing; low concurrency avoids
    # thrashing a single local model.
    ragas_timeout: int = 300
    ragas_max_workers: int = 8


@lru_cache
def get_settings() -> Settings:
    """
    Singleton settings instance.
    Reads .env once.
    """
    return Settings()  # type: ignore
