from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """
    Centralized project configuration.
    Automatically reads .env
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False
    )

    # API Keys
    openrouter_api_key: str
    tavily_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Model
    primary_model: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Storage
    chroma_persist_dir: str = "./data/chroma"

    # Behaviour
    log_level: str = "INFO"
    max_agent_iterations: int = 3
    risk_threshold: float = 0.7


@lru_cache
def get_settings() -> Settings:
    """
    Singleton settings instance.
    Reads .env once.
    """
    return Settings()