"""
Storage backend abstraction for email attachments.

Architecture
────────────
StorageBackend (ABC)
  ├── AzureBlobBackend   — Azure Blob Storage (recommended when running on Azure)
  ├── S3Backend          — AWS S3 / S3-compatible (MinIO, Cloudflare R2, etc.)
  └── LocalFileBackend   — Filesystem (development / CI only)

Usage
─────
backend = get_storage_backend()
key     = await backend.upload(key, data, content_type)
data    = await backend.read(key)
url     = await backend.presigned_url(key, expires_seconds=3600)
await     backend.delete(key)

Keys follow the pattern: attachments/{ticket_id}/{uuid}/{filename}

Configuration (via .env / environment variables)
─────────────────────────────────────────────────
ATTACHMENT_STORAGE_BACKEND  = "azure" | "s3" | "local"  (default: "local")

Azure:
  AZURE_STORAGE_ACCOUNT_NAME
  AZURE_STORAGE_ACCOUNT_KEY   (omit to use Managed Identity)
  AZURE_STORAGE_CONTAINER     (default: "attachments")

S3:
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_REGION                  (default: "us-east-1")
  S3_BUCKET_NAME
  S3_ENDPOINT_URL             (optional, for MinIO / R2)

Local:
  LOCAL_ATTACHMENT_DIR        (default: "/tmp/attachments")
"""

import abc
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class StorageBackend(abc.ABC):
    """Abstract interface all backends must implement."""

    @abc.abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        """Upload *data* under *key*. Returns the storage key."""

    @abc.abstractmethod
    async def read(self, key: str) -> bytes:
        """Download and return the raw bytes for *key*."""

    @abc.abstractmethod
    async def delete(self, key: str) -> None:
        """Delete the object at *key*. Silently ignores missing keys."""

    @abc.abstractmethod
    async def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        """Return a time-limited download URL (for cloud backends)."""

    @abc.abstractmethod
    async def public_url(self, key: str) -> str:
        """
        Return the permanent public URL.
        Raise NotImplementedError when the backend has no public URL
        (e.g. local dev — files are served via the API download endpoint).
        """


# ── Azure Blob Storage ─────────────────────────────────────────────────────────

class AzureBlobBackend(StorageBackend):
    """
    Requires:  pip install azure-storage-blob
    Uses DefaultAzureCredential when AZURE_STORAGE_ACCOUNT_KEY is unset.
    """

    def __init__(
        self,
        account_name: str,
        container: str,
        account_key: str | None = None,
    ) -> None:
        from azure.storage.blob.aio import BlobServiceClient  # type: ignore[import]
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions  # type: ignore[import]

        self._container = container
        self._generate_blob_sas = generate_blob_sas
        self._BlobSasPermissions = BlobSasPermissions
        self._account_name = account_name
        self._account_key = account_key

        if account_key:
            conn_str = (
                f"DefaultEndpointsProtocol=https;"
                f"AccountName={account_name};"
                f"AccountKey={account_key};"
                "EndpointSuffix=core.windows.net"
            )
            self._client = BlobServiceClient.from_connection_string(conn_str)
        else:
            from azure.identity.aio import DefaultAzureCredential  # type: ignore[import]
            self._client = BlobServiceClient(
                account_url=f"https://{account_name}.blob.core.windows.net",
                credential=DefaultAzureCredential(),
            )

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        blob = self._client.get_blob_client(container=self._container, blob=key)
        await blob.upload_blob(
            data, overwrite=True,
            content_settings={"content_type": content_type},
        )
        logger.debug("Azure Blob uploaded: %s (%d bytes)", key, len(data))
        return key

    async def read(self, key: str) -> bytes:
        blob = self._client.get_blob_client(container=self._container, blob=key)
        stream = await blob.download_blob()
        return await stream.readall()

    async def delete(self, key: str) -> None:
        try:
            blob = self._client.get_blob_client(container=self._container, blob=key)
            await blob.delete_blob()
        except Exception:
            pass

    async def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        from datetime import datetime, timezone, timedelta
        if not self._account_key:
            raise NotImplementedError(
                "Presigned URLs require an account key or Managed Identity SAS."
            )
        sas = self._generate_blob_sas(
            account_name=self._account_name,
            container_name=self._container,
            blob_name=key,
            account_key=self._account_key,
            permission=self._BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(seconds=expires_seconds),
        )
        return (
            f"https://{self._account_name}.blob.core.windows.net"
            f"/{self._container}/{key}?{sas}"
        )

    async def public_url(self, key: str) -> str:
        return (
            f"https://{self._account_name}.blob.core.windows.net"
            f"/{self._container}/{key}"
        )


# ── S3 / S3-compatible ─────────────────────────────────────────────────────────

class S3Backend(StorageBackend):
    """
    Requires:  pip install aioboto3
    Compatible with AWS S3, MinIO, Cloudflare R2, Backblaze B2.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        import aioboto3  # type: ignore[import]
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def _client(self):
        kwargs = {}
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
        return self._session.client("s3", **kwargs)

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        async with self._client() as s3:
            await s3.put_object(
                Bucket=self._bucket, Key=key,
                Body=data, ContentType=content_type,
            )
        logger.debug("S3 uploaded: %s (%d bytes)", key, len(data))
        return key

    async def read(self, key: str) -> bytes:
        async with self._client() as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            return await response["Body"].read()

    async def delete(self, key: str) -> None:
        try:
            async with self._client() as s3:
                await s3.delete_object(Bucket=self._bucket, Key=key)
        except Exception:
            pass

    async def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        async with self._client() as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )

    async def public_url(self, key: str) -> str:
        if self._endpoint_url:
            return f"{self._endpoint_url.rstrip('/')}/{self._bucket}/{key}"
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"


# ── Local filesystem (dev only) ────────────────────────────────────────────────

class LocalFileBackend(StorageBackend):
    """
    Writes files to a local directory.
    Files are served back via the API download endpoint, not a public URL.
    NOT suitable for multi-instance deployments.
    """

    def __init__(self, base_dir: str = "/tmp/attachments") -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self._base / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        self._path(key).write_bytes(data)
        logger.debug("LocalFile stored: %s (%d bytes)", key, len(data))
        return key

    async def read(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise FileNotFoundError(f"Local attachment not found: {key}")
        return p.read_bytes()

    async def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except Exception:
            pass

    async def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        # Local files have no URL; callers must use the API download endpoint.
        raise NotImplementedError("LocalFileBackend has no presigned URLs — use the API endpoint.")

    async def public_url(self, key: str) -> str:
        # No public URL for local files — serve via API download endpoint.
        raise NotImplementedError("LocalFileBackend has no public URL — use the API endpoint.")


# ── Factory ────────────────────────────────────────────────────────────────────

def build_storage_key(ticket_id: str, filename: str) -> str:
    """
    Construct a collision-safe object key.
    Format:  attachments/{ticket_id}/{uuid4}/{sanitized_filename}
    """
    safe_name = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in filename
    )[:200]
    return f"attachments/{ticket_id}/{uuid.uuid4()}/{safe_name}"


_backend_singleton: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    """
    Return the configured storage backend (singleton).
    Reads from the app Settings object (which honours .env) on first call.
    """
    global _backend_singleton
    if _backend_singleton is not None:
        return _backend_singleton

    # Import here to avoid circular imports at module load time
    from app.config import get_settings
    settings = get_settings()

    backend_type = settings.ATTACHMENT_STORAGE_BACKEND.lower()

    if backend_type == "azure":
        _backend_singleton = AzureBlobBackend(
            account_name=settings.AZURE_STORAGE_ACCOUNT_NAME or "",
            container=settings.AZURE_STORAGE_CONTAINER,
            account_key=settings.AZURE_STORAGE_ACCOUNT_KEY,
        )
    elif backend_type == "s3":
        _backend_singleton = S3Backend(
            bucket=settings.S3_BUCKET_NAME or "",
            region=settings.AWS_REGION,
            access_key=settings.AWS_ACCESS_KEY_ID,
            secret_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
        )
    else:
        _backend_singleton = LocalFileBackend(
            base_dir=settings.LOCAL_ATTACHMENT_DIR,
        )

    logger.info("Attachment storage backend: %s", type(_backend_singleton).__name__)
    return _backend_singleton
