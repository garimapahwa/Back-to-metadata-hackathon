"""Configuration helpers for DataSheriff."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    openmetadata_host: str = os.getenv("OPENMETADATA_HOST", "http://localhost:8585")
    openmetadata_jwt_token: str = os.getenv("OPENMETADATA_JWT_TOKEN", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    port: int = int(os.getenv("PORT", "8000"))
    environment: str = os.getenv("ENVIRONMENT", "development")

    @property
    def has_openmetadata_auth(self) -> bool:
        """Return True when OpenMetadata credentials are configured."""
        return bool(self.openmetadata_host and self.openmetadata_jwt_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()
