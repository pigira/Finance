"""Application settings, read from environment variables and the optional .env file."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every field maps to an upper-case environment variable (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./data/portfolio.db"
    price_provider: Literal["yahoo", "mock"] = "yahoo"
    fx_provider: Literal["auto", "frankfurter", "yahoo", "mock"] = "auto"
    frankfurter_base_url: str = "https://api.frankfurter.dev/v1"
    http_timeout_seconds: float = 5.0
    price_ttl_minutes: int = 15
    stale_after_hours: int = 24
    background_refresh_hours: float = 6.0
    display_timezone: str = "Europe/Amsterdam"
    # Host names the app answers to (comma separated). See app/security.py.
    allowed_hosts: str = "127.0.0.1,localhost"

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def effective_fx_provider(self) -> str:
        """Resolve "auto": a mock price provider implies a fully offline app."""
        if self.fx_provider != "auto":
            return self.fx_provider
        return "mock" if self.price_provider == "mock" else "frankfurter"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (parsed once)."""
    return Settings()
