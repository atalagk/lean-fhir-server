"""Environment-driven settings (SPEC section 6)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    database_url: str = "sqlite:////data/leanfhir.db"
    base_url: str | None = None
    default_count: int = 20
    max_count: int = 200
    enforce_references: bool = True
    cors_origins: str = ""
    log_level: str = "INFO"
    port: int = 8000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
