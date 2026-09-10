/**
 * Screen 03, and 03a while extraction runs.
 *
 * The one implemented screen. It exists to prove the architecture end to end
 * rather than to be finished: with no business logic written, dropping a PDF
 * here should fail, and the *path* it takes to failing is the deliverable.
 *
 * Browser to storage, statement row, job enqueued, worker claims it, same trace
 * id throughout, terminal status polled back. Every seam crossed.
 *
 * Screen 03a reports four facts rather than showing a spinner, because the next
 * screen's behaviour depends on exactly those values, and a person who can see
 * them can tell a slow import from a stuck one.
 */

import { useState } from "react";
import { ApiError } from "../../api/client";
import { isTerminal, useStatementPolling } from "../../import/usePolling";
import { uploadStatement } from "../../import/upload";

export function ImportScreen() {
  const [statementId, setStatementId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const { data: statement } = useStatementPolling(statementId);

  async function handleFile(file: File) {
    setUploadError(null);
    setIsUploading(true);
    try {
      const registered = await uploadStatement(file);
      setStatementId(registered.id);
    } catch (error) {
      setUploadError(
        error instanceof ApiError
          ? `${error.message}${error.requestId ? ` (${error.requestId})` : ""}`
          : (error as Error).message,
      );
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <section className="screen">
      <h1>Import a statement</h1>
      <p className="muted">
        Upload a statement PDF. Nothing enters the ledger until the rows add up
        to the total printed on the document.
      </p>

      <label className="dropzone">
        <input
          type="file"
          accept="application/pdf"
          disabled={isUploading}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
        <span>{isUploading ? "Uploading…" : "Choose a PDF"}</span>
      </label>

      {uploadError && (
        <p className="error" role="alert">
          {uploadError}
        </p>
      )}

      {statement && (
        <div className="panel">
          <h2>Reading your statement</h2>
          <dl className="facts">
            <dt>Status</dt>
            <dd>{statement.status}</dd>

            <dt>Text layer</dt>
            <dd>{formatBoolean(statement.extraction?.has_text_layer)}</dd>

            <dt>Card</dt>
            <dd>{statement.extraction?.card_identified ?? "not identified"}</dd>

            <dt>Printed total</dt>
            <dd>{statement.extraction?.printed_total ?? "not read"}</dd>

            <dt>Rows extracted</dt>
            <dd>{statement.extraction?.rows_extracted ?? 0}</dd>

            <dt>Parser</dt>
            <dd>{statement.extraction?.parser ?? "not selected"}</dd>
          </dl>

          {statement.failure && (
            <p className="error" role="alert">
              {statement.failure.reason}: {statement.failure.message}
            </p>
          )}

          {statement.trace_id && (
            <p className="muted">
              Trace <code>{statement.trace_id}</code>. Filter the API and worker
              logs by this to see every step of this import.
            </p>
          )}

          {isTerminal(statement.status) && statement.status === "failed" && (
            <p className="muted">
              A failure here is the expected outcome while extraction is
              unwritten. The upload, the statement row, the queued job and the
              worker all worked, which is what this screen is currently proving.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function formatBoolean(value: boolean | undefined): string {
  if (value === undefined) return "unknown";
  return value ? "yes" : "no";
}
