"""S3 implementation, used against MinIO locally and AWS in production.

Two clients, and the reason is the thing most likely to waste an afternoon.

A presigned URL's signature covers the host it was signed for. If the API signs
with the address *it* uses to reach storage, and that address is a container
hostname the browser cannot resolve, the URL is unusable and cannot be repaired
by rewriting the host, because rewriting invalidates the signature. So signing
always uses the browser-reachable endpoint, and server-side operations use the
internal one. On a host-run API the two are equal and this costs nothing. In
AWS both are unset and boto3 derives them from the region.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.storage.base import PresignedUpload

if TYPE_CHECKING:
    from app.config import Settings

#: Refuse anything that is not a PDF at the point the URL is issued, so an
#: unusable object never reaches the bucket.
ALLOWED_CONTENT_TYPES = frozenset({"application/pdf"})

#: A statement PDF is around 250 KB. The ceiling is generous rather than tight,
#: but present: without one, a presigned URL is permission to fill the bucket.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class UploadRejected(ValueError):
    """The requested upload is not one we will issue a URL for."""


class S3ObjectStore:
    """Concrete :class:`~app.storage.base.ObjectStore`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = settings.s3_bucket
        self._internal = self._build_client(endpoint=settings.s3_endpoint)
        signing_endpoint = settings.signing_endpoint
        self._signer = (
            self._internal
            if signing_endpoint == settings.s3_endpoint
            else self._build_client(endpoint=signing_endpoint)
        )

    def _build_client(self, *, endpoint: str | None) -> Any:  # noqa: ANN401
        access_key = self._settings.s3_access_key_id
        secret_key = self._settings.s3_secret_access_key
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=self._settings.s3_region,
            aws_access_key_id=access_key.get_secret_value() if access_key else None,
            aws_secret_access_key=secret_key.get_secret_value() if secret_key else None,
            config=Config(
                # v4 everywhere. MinIO and AWS both accept it, so the signing
                # path is identical in both environments.
                signature_version="s3v4",
                s3={"addressing_style": "path" if self._settings.s3_force_path_style else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    # -- Uploads -----------------------------------------------------------

    def presign_upload(self, *, content_type: str, size_bytes: int) -> PresignedUpload:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise UploadRejected(f"content type {content_type!r} is not accepted")
        if size_bytes <= 0 or size_bytes > MAX_UPLOAD_BYTES:
            raise UploadRejected(f"size {size_bytes} is outside the accepted range")

        upload_id = uuid.uuid4().hex
        key = f"uploads/{upload_id}.pdf"
        expires_in = self._settings.s3_presign_expiry_seconds

        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": key,
            "ContentType": content_type,
        }
        headers = {"Content-Type": content_type}

        # Sent locally as well as in production. Enabling encryption only in
        # production would mean the code path that sets this header first runs
        # in production, which is the wrong place to discover a bucket policy
        # mismatch (TC-SEC-008).
        if self._settings.s3_server_side_encryption:
            params["ServerSideEncryption"] = self._settings.s3_server_side_encryption
            headers["x-amz-server-side-encryption"] = self._settings.s3_server_side_encryption

        url = self._signer.generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )

        return PresignedUpload(
            upload_id=upload_id,
            key=key,
            url=url,
            expires_at_epoch=int(time.time()) + expires_in,
            required_headers=headers,
        )

    # -- Reads -------------------------------------------------------------

    def presign_download(self, *, key: str, expires_in: int | None = None) -> str:
        return self._signer.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in or self._settings.s3_presign_expiry_seconds,
            HttpMethod="GET",
        )

    def get_bytes(self, *, key: str) -> bytes:
        response = self._internal.get_object(Bucket=self._bucket, Key=key)
        body: bytes = response["Body"].read()
        return body

    def exists(self, *, key: str) -> bool:
        try:
            self._internal.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    # -- Deletes -----------------------------------------------------------

    def delete(self, *, key: str) -> None:
        # S3 delete is already idempotent. Stated here because account deletion
        # retries after a partial failure and must not trip over its own work.
        self._internal.delete_object(Bucket=self._bucket, Key=key)

    def delete_prefix(self, *, prefix: str) -> int:
        paginator = self._internal.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if not keys:
                continue
            self._internal.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
            deleted += len(keys)
        return deleted


def build_object_store(settings: Settings) -> S3ObjectStore:
    return S3ObjectStore(settings)
