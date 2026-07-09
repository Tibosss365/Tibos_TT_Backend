from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/helpdesk"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    SECRET_KEY: str = "changeme-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days

    # App
    APP_TITLE: str = "IT Helpdesk API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # SSO / OIDC — frontend URL used to build the post-login redirect
    FRONTEND_URL: str = "http://localhost:5173"
    # Backend public URL — used to build SAML SP metadata URLs (Entity ID, ACS URL, etc.)
    # Set this to your deployed API URL, e.g. https://tibos-tt-api.azurewebsites.net
    BACKEND_URL: str = "https://tibos-tt-api.azurewebsites.net"

    # Attachment object storage
    # backend: "azure" | "s3" | "local"
    ATTACHMENT_STORAGE_BACKEND: str = "local"

    # Azure Blob Storage
    AZURE_STORAGE_ACCOUNT_NAME: Optional[str] = None
    AZURE_STORAGE_ACCOUNT_KEY: Optional[str] = None
    AZURE_STORAGE_CONTAINER: str = "attachments"

    # AWS S3 / S3-compatible
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: Optional[str] = None
    S3_ENDPOINT_URL: Optional[str] = None

    # Local dev
    LOCAL_ATTACHMENT_DIR: str = "/tmp/attachments"

    # AI features (email inbox suggest-reply / summarize). Optional — the
    # /email/ai/* endpoints return 503 when unset.
    ANTHROPIC_API_KEY: Optional[str] = None
    AI_MODEL: str = "claude-opus-4-8"

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
