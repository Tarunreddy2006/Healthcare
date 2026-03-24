"""
database.py — Guardian AI
Async MySQL connection pool via aiomysql.
Provides a FastAPI dependency and one-time schema initialisation.
"""

import aiomysql
import hashlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Connection pool (created once at startup) ─────────────────────────────────
_pool: Optional[aiomysql.Pool] = None


async def create_pool() -> None:
    """Initialise the connection pool. Called from app lifespan."""
    global _pool
    _pool = await aiomysql.create_pool(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        db=settings.MYSQL_DATABASE,
        minsize=2,
        maxsize=10,
        autocommit=False,
        charset="utf8mb4",
    )
    logger.info("MySQL connection pool created.")


async def close_pool() -> None:
    """Gracefully close the pool. Called from app lifespan."""
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        logger.info("MySQL connection pool closed.")


@asynccontextmanager
async def get_connection() -> AsyncGenerator[aiomysql.Connection, None]:
    """Yield a single connection from the pool (with auto-rollback on error)."""
    async with _pool.acquire() as conn:
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


# ── FastAPI dependency ────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[aiomysql.Connection, None]:
    """Dependency-injected DB connection for route handlers."""
    async with get_connection() as conn:
        yield conn


# ── Utility helpers ───────────────────────────────────────────────────────────
def hash_dob(dob: str) -> str:
    """
    One-way SHA-256 hash of the date-of-birth string.
    dob format expected: 'YYYY-MM-DD'
    """
    return hashlib.sha256(dob.strip().encode()).hexdigest()


# ── DDL — run once on first deploy ───────────────────────────────────────────
CREATE_PATIENTS_TABLE = """
CREATE TABLE IF NOT EXISTS patients (
    uid         VARCHAR(36)  NOT NULL PRIMARY KEY,  -- e.g. UUID or student roll number
    full_name   VARCHAR(120) NOT NULL,
    gender      ENUM('M','F','Other') NOT NULL,
    dob_hash    CHAR(64)     NOT NULL,               -- SHA-256 of 'YYYY-MM-DD'
    created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_VISITS_TABLE = """
CREATE TABLE IF NOT EXISTS visits (
    id              BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    patient_uid     VARCHAR(36)  NOT NULL,
    visit_date      DATE         NOT NULL,
    symptoms        TEXT,
    diagnosis       TEXT,
    medicines       JSON,        -- list of {name, dosage, duration}
    precautions     TEXT,
    attending_doctor VARCHAR(120),
    membrain_node_id VARCHAR(80), -- ID returned by Membrain after storing this visit
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_uid) REFERENCES patients(uid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_ALLERGIES_TABLE = """
CREATE TABLE IF NOT EXISTS allergies (
    id          BIGINT      NOT NULL AUTO_INCREMENT PRIMARY KEY,
    patient_uid VARCHAR(36) NOT NULL,
    allergen    VARCHAR(120) NOT NULL,
    reaction    VARCHAR(200),
    severity    ENUM('mild','moderate','severe','life-threatening') DEFAULT 'moderate',
    recorded_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_uid) REFERENCES patients(uid) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def init_schema() -> None:
    """Create tables if they do not already exist."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            for ddl in [CREATE_PATIENTS_TABLE, CREATE_VISITS_TABLE, CREATE_ALLERGIES_TABLE]:
                await cur.execute(ddl)
    logger.info("Database schema initialised.")
