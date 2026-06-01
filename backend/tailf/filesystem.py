"""The ``FileSystem`` abstraction — the only persistence boundary.

The pipeline and store talk ONLY to this interface; they never touch
``pathlib`` or ``boto3`` directly. The DI module binds one of the two
implementations by environment:

- :class:`LocalFileSystem` (root ``.data/``) for local dev,
- :class:`S3FileSystem` (a bucket) inside Lambda.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mypy_boto3_s3.client import S3Client


class FileSystem(ABC):
    """Minimal key/blob store: read, write, and existence checks.

    Keys are storage-relative (e.g. ``"data.json"``). Text helpers assume
    UTF-8; byte helpers are provided for completeness.
    """

    @abstractmethod
    def read_text(self, key: str) -> str:
        """Return the UTF-8 contents at ``key``. Raises if it does not exist."""

    @abstractmethod
    def write_text(self, key: str, content: str) -> None:
        """Write UTF-8 ``content`` to ``key``, creating parents as needed."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return whether an object exists at ``key``."""

    # --- byte variants (optional convenience) ----------------------------

    def read_bytes(self, key: str) -> bytes:
        return self.read_text(key).encode("utf-8")

    def write_bytes(self, key: str, content: bytes) -> None:
        self.write_text(key, content.decode("utf-8"))


class LocalFileSystem(FileSystem):
    """Filesystem-backed store rooted at a directory (the ``.data/`` stand-in)."""

    def __init__(self, root: str | Path = ".data") -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def read_text(self, key: str) -> str:
        return self._path(key).read_text(encoding="utf-8")

    def write_text(self, key: str, content: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_bytes(self, key: str, content: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class S3FileSystem(FileSystem):
    """S3-backed store for a single bucket, used inside Lambda.

    ``content_type`` is applied on writes so CloudFront serves ``data.json``
    with the right ``Content-Type``.
    """

    def __init__(
        self,
        bucket: str,
        client: S3Client,
        *,
        content_type: str = "application/json",
    ) -> None:
        self.bucket = bucket
        self.client = client
        self.content_type = content_type

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def read_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def write_text(self, key: str, content: str) -> None:
        self.write_bytes(key, content.encode("utf-8"))

    def write_bytes(self, key: str, content: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=self.content_type,
        )

    def exists(self, key: str) -> bool:
        # botocore is imported lazily so the module imports without boto3
        # available (e.g. when only running the model/store tests).
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
