"""The object storage seam.

PDFs never pass through the API tier. The browser uploads straight to the
bucket with a presigned URL, and the API only ever handles pointers. That is
why concurrent uploads are a storage concern rather than an API memory concern,
and it is the reason this Protocol exists: one implementation talks S3, and
whether that is MinIO on a laptop or a bucket in Singapore is configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """A short-lived permission to write exactly one object.

    The URL is a bearer credential. Anyone holding it can write that key until
    it expires, which is why it must never be logged (ADR-014) and why the key
    is generated server-side rather than accepted from the client.
    """

    upload_id: str
    key: str
    url: str
    expires_at_epoch: int
    required_headers: dict[str, str]


class ObjectStore(Protocol):
    """Operations the application needs from object storage."""

    def presign_upload(self, *, content_type: str, size_bytes: int) -> PresignedUpload:
        """Issue a URL permitting a single PUT of one object."""
        ...

    def presign_download(self, *, key: str, expires_in: int | None = None) -> str:
        """Issue a short-lived read URL, used for the source-page view."""
        ...

    def get_bytes(self, *, key: str) -> bytes:
        """Fetch an object. The worker uses this; the API tier does not."""
        ...

    def exists(self, *, key: str) -> bool:
        ...

    def delete(self, *, key: str) -> None:
        ...

    def delete_prefix(self, *, prefix: str) -> int:
        """Delete every object under a prefix, returning the count.

        Account deletion spans the database and the bucket, so it runs as an
        idempotent job rather than inside a request. Deleting an absent object
        must succeed, or a retry after a partial failure cannot complete.
        """
        ...
