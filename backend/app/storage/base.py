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


def derive_object_key(upload_id: str) -> str:
    """The storage key a given ``upload_id`` was signed for.

    A pure function of ``upload_id`` rather than a lookup, and deliberately
    the *same* formula :meth:`~app.storage.s3.S3ObjectStore.presign_upload`
    used to build the key in the first place: the API only ever receives
    ``upload_id`` from the client (never ``key``), so recomputing it here is
    what lets ``register`` confirm the object exists without trusting a
    client-supplied path into the bucket.
    """
    return f"uploads/{upload_id}.pdf"


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

    def put_bytes(self, *, key: str, data: bytes, content_type: str) -> None:
        """Write an object the server itself generated -- an export CSV, not
        a statement PDF. Statement uploads stay browser-to-bucket; this is
        the one path where the API is the one holding the bytes already, and
        proxying them through a presigned URL back to itself would buy
        nothing (BUILD STEP 9.3)."""
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
