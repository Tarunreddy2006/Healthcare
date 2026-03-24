"""
config.py — Guardian AI
Centralised configuration loaded from environment variables.
All secrets live in a .env file; nothing is hard-coded.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── Application ───────────────────────────────────────────────
    APP_NAME: str = "Guardian AI — Clinical Memory Bridge"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── MySQL ─────────────────────────────────────────────────────
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "guardian_user"
    MYSQL_PASSWORD: str = "change_me"
    MYSQL_DATABASE: str = "guardian_ai"

    # ── AlphaNimble Membrain ──────────────────────────────────────
    MEMBRAIN_BASE_URL: str = "https://api.alphanimble.io/membrain/v1"
    MEMBRAIN_API_KEY: str = "your_membrain_api_key_here"

    # Importance score thresholds (AlphaNimble philosophy)
    IMPORTANCE_CRITICAL: float = 1.0   # Allergies, life-threatening conditions
    IMPORTANCE_HIGH: float = 0.85      # Chronic diseases, recurring symptoms
    IMPORTANCE_MEDIUM: float = 0.65    # Regular prescriptions, diagnoses
    IMPORTANCE_LOW: float = 0.40       # Minor one-off observations

    # Memory decay: critical memories never decay below this floor
    DECAY_FLOOR_CRITICAL: float = 0.90

    # ── Gemini ────────────────────────────────────────────────────
    GEMINI_API_KEY: str = "your_gemini_api_key_here"
    GEMINI_MODEL: str = "gemini-3-flash"

    # ── Security ──────────────────────────────────────────────────
    SECRET_KEY: str = "replace_with_a_strong_random_secret"
    TOKEN_EXPIRE_MINUTES: int = 60

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Return a cached singleton of Settings."""
    return Settings()