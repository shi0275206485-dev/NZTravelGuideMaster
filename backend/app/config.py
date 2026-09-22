"""Application configuration, loaded from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM (the only service requiring a key) ---
    # No defaults for the key or the workspace endpoint: both are secrets
    # tied to one account, and a default in source is a secret in git. They
    # come from backend/.env locally and from the server's .env in
    # deployment. With the key unset the agents degrade — significance
    # ranking and the fallback planner — rather than failing to start.
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.5-flash"
    llm_timeout_s: float = 60.0
    # Fallback selected in Phase 1; switching is a config change, not a code change.
    llm_fallback_model: str = "glm-4.7-flash"

    # --- Geo / weather services ---
    # Overpass and Nominatim are development-time only: destination POIs and
    # coordinates are pre-fetched into the cache, so the running app does not
    # call these rate-limited public services.
    overpass_url: str = "http://localhost:12345/api/interpreter"
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    # The public demo server: fine for a course demo at low volume, and its
    # usage policy asks for no more than one request a second. HTTPS so the
    # deployed app makes no plaintext calls.
    osrm_url: str = "https://router.project-osrm.org"
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"

    # --- Storage ---
    cache_db_path: Path = Path("data/cache.sqlite")

    # --- Access control (FR10) ---
    demo_access_code: str = ""
    # Enforced in app/quota.py; see there for why each exists. On by
    # default, and independent of the access code, so a deployment that
    # forgets to set a code is still capped rather than wide open. Set
    # RATE_LIMIT_GENERATE=off in a local .env to generate freely while
    # developing.
    rate_limit_generate: str = "3/hour"      # per client address
    daily_generation_cap: int = 30           # across everyone; 0 disables
    access_code_attempts: str = "10/hour"    # wrong codes per client address

    # --- CORS ---
    frontend_origin: str = "http://localhost:5173"

    @property
    def access_control_enabled(self) -> bool:
        """Local development runs without a code; deployment must set one."""
        return bool(self.demo_access_code)


@lru_cache
def get_settings() -> Settings:
    return Settings()
