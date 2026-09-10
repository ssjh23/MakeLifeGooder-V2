/**
 * Direct upload to object storage.
 *
 * This module deliberately does not use the generated API client, and that is
 * the point rather than an oversight. The file is PUT straight to the bucket
 * with a presigned URL, so PDF bytes never reach the API tier. Concurrent
 * uploads become a storage concern rather than an API memory concern, and a
 * large statement does not occupy a request worker while it transfers.
 *
 * Two things about presigned URLs that matter here:
 *
 * The signature covers the host and the headers. The backend signs with the
 * browser-reachable endpoint, and the headers it returns must be sent exactly
 * as given. Adding one, dropping one, or letting the browser set a different
 * content type produces a signature mismatch, and the error from storage does
 * not explain that.
 *
 * The URL is a bearer credential. Anyone holding it can write that object until
 * it expires, so it is never logged, never put in an analytics event, and never
 * added to a URL the user can copy.
 */

import { api, toApiError } from "../api/client";

export interface UploadResult {
  uploadId: string;
}

/** Ask the API for permission to write one object. */
export async function requestUploadUrl(file: File) {
  const { data, response } = await api.POST("/api/v1/statements/upload-url", {
    body: { content_type: file.type || "application/pdf", size_bytes: file.size },
  });
  if (!data) throw await toApiError(response);
  return data;
}

/** PUT the file to storage. Bypasses the API entirely. */
export async function putToStorage(
  file: File,
  target: { url: string; required_headers: Record<string, string> },
): Promise<void> {
  const response = await fetch(target.url, {
    method: "PUT",
    body: file,
    // Exactly the headers the backend signed. No more, no fewer.
    headers: target.required_headers,
    // No cookies. This is a different origin and the presigned URL is the only
    // credential involved; sending session cookies to object storage would be
    // both useless and careless.
    credentials: "omit",
  });

  if (!response.ok) {
    // Deliberately not including the URL in the message: it is a credential,
    // and error messages end up in logs and screenshots.
    throw new Error(
      `Upload failed with ${response.status}. If this persists, check the bucket's CORS rule.`,
    );
  }
}

/** Register the uploaded object, which creates the statement and enqueues work. */
export async function registerStatement(uploadId: string, cardId?: string) {
  const { data, response } = await api.POST("/api/v1/statements", {
    body: { upload_id: uploadId, card_id: cardId ?? null },
  });
  if (!data) throw await toApiError(response);
  return data;
}

/** The whole upload path, in the order the sequence diagram gives it. */
export async function uploadStatement(file: File, cardId?: string) {
  const presigned = await requestUploadUrl(file);
  await putToStorage(file, presigned);
  return registerStatement(presigned.upload_id, cardId);
}
