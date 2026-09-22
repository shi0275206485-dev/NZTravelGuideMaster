"""Application configuration, loaded from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM (the only service requiring a key) ---
    llm_api_key: str = "sk-ws-H.XIYPRE.oLtv.MEUCIQCll6_7SslEU1dgLNckqg4oQOc5Af3BRW17fDFlfcnbvAIgSO2GdpMrKLcr-pRYM0Ds6x2PWwDG4f2cq-3WHinz6cE"
    llm_base_url: str = "https://ws-aagpgukz5pwxc3g4.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
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
    osrm_url: str = "http://router.project-osrm.org"
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"

    # --- Storage ---
    cache_db_path: Path = Path("data/cache.sqlite")

    # --- Access control (FR10) ---
    demo_access_code: str = ""
    rate_limit_generate: str = "3/hour"
    daily_generation_cap: int = 30

    # --- CORS ---
    frontend_origin: str = "http://localhost:5173"

    @property
    def access_control_enabled(self) -> bool:
        """Local development runs without a code; deployment must set one."""
        return bool(self.demo_access_code)


@lru_cache
def get_settings() -> Settings:
    return Settings()
