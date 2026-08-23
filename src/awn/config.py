"""Application configuration loaded from the environment."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime settings.

    OpenAI credentials are only required when the OpenAI gateway is selected. The
    fake gateway remains the safe default for local development and automated tests.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AWN_",
        extra="ignore",
    )

    app_name: str = "Awn"
    environment: Literal["local", "test", "production"] = "local"
    api_prefix: str = "/api/v1"
    model_provider: Literal["fake", "openai"] = "fake"
    openai_model: str | None = None
    openai_api_key: SecretStr | None = None
    database_url: SecretStr = SecretStr("postgresql+psycopg://awn:awn@localhost:5432/awn")

    @model_validator(mode="after")
    def require_openai_configuration(self) -> Self:
        if self.model_provider == "openai":
            if self.openai_api_key is None:
                raise ValueError("AWN_OPENAI_API_KEY is required for the OpenAI provider")
            if not self.openai_model:
                raise ValueError("AWN_OPENAI_MODEL is required for the OpenAI provider")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable configuration snapshot."""

    return Settings()
