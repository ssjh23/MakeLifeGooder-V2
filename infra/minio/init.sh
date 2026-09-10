#!/bin/sh
# Creates the bucket the API expects and turns on the protections that the
# real bucket must also have. Each line here has a counterpart in the cloud
# checklist, so the local store is not friendlier than production.
set -eu

until mc alias set local http://minio:9000 minioadmin minioadmin >/dev/null 2>&1; do
  echo "waiting for minio..."
  sleep 1
done

mc mb --ignore-existing local/ledger-statements

# No anonymous access. The only way to reach an object is a presigned URL,
# which is a bearer credential and must never be logged (ADR-014).
mc anonymous set none local/ledger-statements

# Default encryption, so an upload that forgets the header is still encrypted
# and TC-SEC-008 is meaningful before the first deploy.
mc encrypt set sse-s3 local/ledger-statements || \
  echo "note: could not set default encryption; check MINIO_KMS_SECRET_KEY"

echo "minio ready: bucket ledger-statements"
