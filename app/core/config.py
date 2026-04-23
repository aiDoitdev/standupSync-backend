from functools import lru_cache
from typing import Literal
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Core ─────────────────────────────────────────────────────────────────
    environment: Literal["development", "production", "test"] = "development"
    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_days: int = 7

    # ── Frontend ──────────────────────────────────────────────────────────────
    frontend_url: str = "http://localhost:3000"

    # ── Email ─────────────────────────────────────────────────────────────────
    resend_api_key: str = ""
    email_from: str = "StandupSync <noreply@aidoit.dev>"

    # ── Billing — Lemon Squeezy ───────────────────────────────────────────────
    lemonsqueezy_api_key: str = ""
    lemonsqueezy_webhook_secret: str = ""
    lemonsqueezy_store_id: str = ""
    lemonsqueezy_starter_variant_id: str = ""

    # ── LLM ───────────────────────────────────────────────────────────────────
    llm_provider: Literal["openai", "anthropic", "mock"] = "mock"
    llm_api_key: str = ""
    llm_model: str = ""

    # ── Feature flags ─────────────────────────────────────────────────────────
    ai_task_radar_admin_run: bool = False

    # ── Computed ──────────────────────────────────────────────────────────────
    database_url_async: str = ""

    @field_validator("jwt_secret")
    @classmethod
    def _require_jwt_secret(cls, v: str) -> str:
        if not v or v in ("changeme", "changeme-insecure-default-key", "secret"):
            raise ValueError(
                "JWT_SECRET must be set to a strong random value (min 32 chars). "
                "Run: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @model_validator(mode="after")
    def _build_async_url(self) -> "Settings":
        self.database_url_async = self.database_url.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def allowed_origins(self) -> list[str]:
        if self.is_production:
            return [self.frontend_url]
        return ["*"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
