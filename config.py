from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # ── Pydantic Config ────────────────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid"   # Strict: prevents unknown env variables
    )

    # ── Application ───────────────────────────────────────────────
    APP_NAME: str = "Guardian AI — Clinical Memory Bridge"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── MySQL ─────────────────────────────────────────────────────
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    MYSQL_DATABASE: str

    
    MEMBRAIN_BASE_URL: str
    MEMBRAIN_API_KEY: str
    
    IMPORTANCE_CRITICAL: float = 1.0
    IMPORTANCE_HIGH: float = 0.85
    IMPORTANCE_MEDIUM: float = 0.65
    IMPORTANCE_LOW: float = 0.40

    DECAY_FLOOR_CRITICAL: float = 0.90

    # ── Gemini ──
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # ── Security ──────
    SECRET_KEY: str
    TOKEN_EXPIRE_MINUTES: int = 60


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance (singleton)."""
    return Settings()
