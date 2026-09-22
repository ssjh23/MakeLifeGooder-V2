/**
 * Screens 03 to 03d.
 *
 * One statement, one URL: `/import` is the upload form, `/import/:statementId`
 * is everything that happens to that statement afterwards. There is no
 * separate "committed" flag on `StatementResponse` — `status === "ready"`
 * only ever happens the instant `commit()` succeeds (see
 * `app/services/statement.py`), so it doubles as "this belongs on the
 * receipt screen, not the reconciliation one."
 */

import { useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  api,
  asDuplicateFileConflict,
  asDuplicateStatementConflict,
  toApiError,
} from "../../api/client";
import type {
  CardResponse,
  ImportSummary,
  RowResponse,
  RowsResponse,
  StatementResponse,
} from "../../api/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDate, formatMoney } from "../../shared/money";
import { isTerminal, useStatementPolling } from "../../import/usePolling";
import { putToStorage, registerStatement, requestUploadUrl } from "../../import/upload";

export function ImportFeature() {
  return (
    <Routes>
      <Route path="/" element={<UploadScreen />} />
      <Route path="/:statementId" element={<StatementDetail />} />
    </Routes>
  );
}

// -- Upload, screen 03 -------------------------------------------------------

function UploadScreen() {
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  // Set when registration reports this exact file was uploaded before.
  // Keeps the already-PUT upload_id so "replace" only has to retry
  // registration, not re-upload the bytes.
  const [duplicateConflict, setDuplicateConflict] = useState<{
    existingStatementId: string;
    uploadId: string;
  } | null>(null);
  const navigate = useNavigate();

  async function handleFile(file: File) {
    setUploadError(null);
    setDuplicateConflict(null);
    setIsUploading(true);
    try {
      const presigned = await requestUploadUrl(file);
      await putToStorage(file, presigned);
      const registered = await tryRegister(presigned.upload_id);
      if (registered) navigate(`/import/${registered.id}`);
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

  /** Registers `uploadId`, or -- on a duplicate-file conflict -- sets
   * `duplicateConflict` and returns null instead of throwing, so the
   * caller can show the warning rather than a generic error. */
  async function tryRegister(uploadId: string): Promise<StatementResponse | null> {
    try {
      return await registerStatement(uploadId);
    } catch (error) {
      if (error instanceof ApiError) {
        const conflict = asDuplicateFileConflict(error);
        if (conflict) {
          setDuplicateConflict({ existingStatementId: conflict.existing_statement_id, uploadId });
          return null;
        }
      }
      throw error;
    }
  }

  async function handleReplace() {
    if (!duplicateConflict) return;
    setUploadError(null);
    setIsUploading(true);
    try {
      const { response } = await api.DELETE("/api/v1/statements/{statement_id}", {
        params: { path: { statement_id: duplicateConflict.existingStatementId } },
        body: { confirm: true },
      });
      if (!response.ok) throw await toApiError(response);
      const registered = await registerStatement(duplicateConflict.uploadId);
      setDuplicateConflict(null);
      navigate(`/import/${registered.id}`);
    } catch (error) {
      setUploadError(error instanceof ApiError ? error.message : "Could not replace the statement.");
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

      {duplicateConflict && (
        <div className="banner">
          <p className="error">
            You've already uploaded this exact file
            (<code>{duplicateConflict.existingStatementId}</code>).
          </p>
          <p className="muted">
            Replacing it removes that statement and everything imported from it.
            This can't be undone.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button--danger"
              disabled={isUploading}
              onClick={() => void handleReplace()}
            >
              {isUploading ? "Replacing…" : "Replace it and continue"}
            </button>
            <button
              type="button"
              className="button--link"
              onClick={() => setDuplicateConflict(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

// -- Statement detail, screens 03a to 03d and the 04c receipt ---------------

function StatementDetail() {
  const { statementId } = useParams<{ statementId: string }>();
  const { data: statement } = useStatementPolling(statementId ?? null);

  if (!statement) return null;

  switch (statement.status) {
    case "pending":
    case "processing":
      return <ExtractingPanel statement={statement} />;
    case "failed":
      return <FailedPanel statement={statement} />;
    case "needs_review":
      return <ReconcilePanel statement={statement} />;
    case "ready":
      return <ReceiptPanel statement={statement} />;
    default:
      return null;
  }
}

function formatBoolean(value: boolean | undefined): string {
  if (value === undefined) return "unknown";
  return value ? "yes" : "no";
}

function ExtractingPanel({ statement }: { statement: StatementResponse }) {
  return (
    <section className="screen">
      <h1>Reading your statement</h1>
      <div className="panel">
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

        {statement.trace_id && (
          <p className="muted">
            Trace <code>{statement.trace_id}</code>. Filter the API and worker
            logs by this to see every step of this import.
          </p>
        )}
      </div>
    </section>
  );
}

// -- Failure recovery, screen 03c --------------------------------------------

function FailedPanel({ statement }: { statement: StatementResponse }) {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const unlock = useMutation({
    mutationFn: async () => {
      const { response } = await api.POST("/api/v1/statements/{statement_id}/unlock", {
        params: { path: { statement_id: statement.id } },
        body: { password },
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["statement", statement.id] }),
  });

  const discard = useMutation({
    mutationFn: async () => {
      const { response } = await api.POST("/api/v1/statements/{statement_id}/discard", {
        params: { path: { statement_id: statement.id } },
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: () => navigate("/import"),
  });

  async function handleUnlock(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await unlock.mutateAsync();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not unlock this statement.");
    }
  }

  const reason = statement.failure?.reason;

  return (
    <section className="screen">
      <h1>This statement couldn't be read</h1>
      <div className="panel">
        <p className="error" role="alert">
          {statement.failure?.message ?? "Extraction failed."}
        </p>

        {reason === "password_protected" ? (
          <form className="form" onSubmit={handleUnlock}>
            <div className="field">
              <label htmlFor="pdf-password">PDF password</label>
              <input
                id="pdf-password"
                type="password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <div className="button-row">
              <button type="submit" className="button--primary" disabled={unlock.isPending}>
                {unlock.isPending ? "Retrying…" : "Unlock and retry"}
              </button>
            </div>
          </form>
        ) : (
          <div className="button-row">
            <button
              type="button"
              className="button--danger"
              onClick={() => void discard.mutateAsync()}
              disabled={discard.isPending}
            >
              Discard and start over
            </button>
            <Link to="/import" className="button">
              Back to import
            </Link>
          </div>
        )}

        {statement.trace_id && (
          <p className="muted">
            Trace <code>{statement.trace_id}</code>.
          </p>
        )}
      </div>
    </section>
  );
}

// -- Reconciliation, screen 03b -----------------------------------------------

function ReconcilePanel({ statement }: { statement: StatementResponse }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [commitError, setCommitError] = useState<string | null>(null);
  const [acceptGap, setAcceptGap] = useState(false);
  const [gapReason, setGapReason] = useState("");
  const [duplicateOptions, setDuplicateOptions] = useState<string[] | null>(null);
  const [duplicateExistingId, setDuplicateExistingId] = useState<string | null>(null);
  const [targetCardId, setTargetCardId] = useState("");

  const rowsQuery = useQuery({
    queryKey: ["statement-rows", statement.id],
    queryFn: async (): Promise<RowsResponse> => {
      const { data, response } = await api.GET("/api/v1/statements/{statement_id}/rows", {
        params: { path: { statement_id: statement.id } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  const cardsQuery = useQuery({
    queryKey: ["cards"],
    queryFn: async (): Promise<CardResponse[]> => {
      const { data, response } = await api.GET("/api/v1/cards", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  function invalidateRows() {
    return queryClient.invalidateQueries({ queryKey: ["statement-rows", statement.id] });
  }

  const tagCard = useMutation({
    mutationFn: async (cardId: string) => {
      const { data, response } = await api.PATCH("/api/v1/statements/{statement_id}", {
        params: { path: { statement_id: statement.id } },
        body: { card_id: cardId },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["statement", statement.id] }),
  });

  const skipRow = useMutation({
    mutationFn: async (rowId: string) => {
      const { response } = await api.POST(
        "/api/v1/statements/{statement_id}/rows/{row_id}/skip",
        { params: { path: { statement_id: statement.id, row_id: rowId } } },
      );
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: invalidateRows,
  });

  const deleteRow = useMutation({
    mutationFn: async (rowId: string) => {
      const { response } = await api.DELETE(
        "/api/v1/statements/{statement_id}/rows/{row_id}",
        { params: { path: { statement_id: statement.id, row_id: rowId } } },
      );
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: invalidateRows,
  });

  const undoRow = useMutation({
    mutationFn: async (rowId: string) => {
      const { response } = await api.POST(
        "/api/v1/statements/{statement_id}/rows/{row_id}/undo",
        { params: { path: { statement_id: statement.id, row_id: rowId } } },
      );
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: invalidateRows,
  });

  const addRow = useMutation({
    mutationFn: async (input: { posted_on: string; description: string; amount: string }) => {
      const { response } = await api.POST("/api/v1/statements/{statement_id}/rows", {
        params: { path: { statement_id: statement.id } },
        body: input,
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: invalidateRows,
  });

  const resolveDuplicate = useMutation({
    mutationFn: async (input: { action: "replace" | "keep_both" | "cancel"; targetCardId?: string }) => {
      const { response } = await api.POST(
        "/api/v1/statements/{statement_id}/resolve-duplicate",
        {
          params: { path: { statement_id: statement.id } },
          body: {
            action: input.action,
            target_card_id: input.targetCardId ?? null,
          },
        },
      );
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: (_data, input) => {
      setDuplicateOptions(null);
      if (input.action === "cancel") {
        navigate("/import");
        return;
      }
      queryClient.invalidateQueries({ queryKey: ["statement", statement.id] });
      queryClient.invalidateQueries({ queryKey: ["statement-rows", statement.id] });
    },
  });

  const commit = useMutation({
    mutationFn: async () => {
      const { data, response } = await api.POST("/api/v1/statements/{statement_id}/commit", {
        params: { path: { statement_id: statement.id } },
        body: acceptGap
          ? { accept_gap: true, gap_reason: gapReason }
          : { accept_gap: false },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["statement", statement.id] });
      // has_statements may just have flipped for the first time.
      queryClient.invalidateQueries({ queryKey: ["session"] });
    },
  });

  async function handleCommit() {
    setCommitError(null);
    setDuplicateOptions(null);
    try {
      await commit.mutateAsync();
    } catch (err) {
      if (err instanceof ApiError) {
        const duplicate = asDuplicateStatementConflict(err);
        if (duplicate) {
          setDuplicateExistingId(duplicate.existing_statement_id);
          setDuplicateOptions(duplicate.resolutions);
          return;
        }
        if (err.isInvalidState) {
          setCommitError(err.message);
          return;
        }
      }
      setCommitError(err instanceof ApiError ? err.message : "Could not commit this statement.");
    }
  }

  if (rowsQuery.error) {
    return (
      <p className="error" role="alert">
        {rowsQuery.error instanceof ApiError ? rowsQuery.error.message : "Could not load rows."}
      </p>
    );
  }
  if (!rowsQuery.data) return null;

  const { rows, reconciliation } = rowsQuery.data;
  const otherCards = (cardsQuery.data ?? []).filter((card) => card.id !== statement.card_id);

  return (
    <section className="screen">
      <h1>Check the rows</h1>
      <p className="muted">
        {rows.length} row{rows.length === 1 ? "" : "s"} extracted. Skip a row that
        isn't a real transaction, or add one the parser missed.
      </p>

      {statement.card_id == null && (
        <div className="banner">
          <p>This statement isn't tagged to a card yet. It can't be committed until it is.</p>
          <div className="field-row">
            <select
              value={targetCardId}
              onChange={(event) => setTargetCardId(event.target.value)}
            >
              <option value="">Choose a card…</option>
              {(cardsQuery.data ?? []).map((card) => (
                <option key={card.id} value={card.id}>
                  {card.nickname} •••• {card.last4}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!targetCardId || tagCard.isPending}
              onClick={() => tagCard.mutate(targetCardId)}
            >
              Tag card
            </button>
          </div>
        </div>
      )}

      <table className="table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Description</th>
            <th>Amount</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <RowLine
              key={row.id}
              row={row}
              currency={reconciliation.currency}
              onSkip={() => skipRow.mutate(row.id)}
              onDelete={() => deleteRow.mutate(row.id)}
              onUndo={() => undoRow.mutate(row.id)}
            />
          ))}
        </tbody>
      </table>

      <AddRowForm
        onAdd={(input) => addRow.mutate(input)}
        isPending={addRow.isPending}
      />

      <div className="panel">
        <h2>Reconciliation</h2>
        <dl className="facts">
          <dt>Extracted total</dt>
          <dd>{formatMoney(reconciliation.extracted_total, reconciliation.currency)}</dd>
          <dt>Printed total</dt>
          <dd>{formatMoney(reconciliation.printed_total, reconciliation.currency)}</dd>
          <dt>Difference</dt>
          <dd>{formatMoney(reconciliation.difference, reconciliation.currency)}</dd>
          <dt>Reconciled</dt>
          <dd>{reconciliation.reconciled ? "yes" : "no"}</dd>
        </dl>

        {!reconciliation.reconciled && (
          <div className="checkbox-field">
            <input
              id="accept-gap"
              type="checkbox"
              checked={acceptGap}
              onChange={(event) => setAcceptGap(event.target.checked)}
            />
            <label htmlFor="accept-gap">
              Record the difference as a gap and commit anyway
            </label>
          </div>
        )}
        {acceptGap && (
          <div className="field">
            <label htmlFor="gap-reason">Reason</label>
            <textarea
              id="gap-reason"
              required
              value={gapReason}
              onChange={(event) => setGapReason(event.target.value)}
            />
          </div>
        )}

        {commitError && (
          <p className="error" role="alert">
            {commitError}
          </p>
        )}

        {duplicateOptions && (
          <div className="banner">
            <p>
              A statement for this card and period already exists
              {duplicateExistingId && (
                <>
                  {" "}
                  (<code>{duplicateExistingId}</code>)
                </>
              )}
              .
            </p>
            <div className="button-row">
              {duplicateOptions.includes("replace") && (
                <button
                  type="button"
                  onClick={() => resolveDuplicate.mutate({ action: "replace" })}
                >
                  Replace it
                </button>
              )}
              {duplicateOptions.includes("keep_both") && (
                <>
                  <select
                    value={targetCardId}
                    onChange={(event) => setTargetCardId(event.target.value)}
                  >
                    <option value="">Assign to a different card…</option>
                    {otherCards.map((card) => (
                      <option key={card.id} value={card.id}>
                        {card.nickname} •••• {card.last4}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    disabled={!targetCardId}
                    onClick={() =>
                      resolveDuplicate.mutate({ action: "keep_both", targetCardId })
                    }
                  >
                    Keep both
                  </button>
                </>
              )}
              {duplicateOptions.includes("cancel") && (
                <button
                  type="button"
                  className="button--danger"
                  onClick={() => resolveDuplicate.mutate({ action: "cancel" })}
                >
                  Cancel this import
                </button>
              )}
            </div>
          </div>
        )}

        <div className="button-row">
          <button
            type="button"
            className="button--primary"
            disabled={
              commit.isPending ||
              statement.card_id == null ||
              (!reconciliation.reconciled && (!acceptGap || !gapReason))
            }
            onClick={() => void handleCommit()}
          >
            {commit.isPending ? "Committing…" : "Commit statement"}
          </button>
        </div>
      </div>
    </section>
  );
}

function RowLine({
  row,
  currency,
  onSkip,
  onDelete,
  onUndo,
}: {
  row: RowResponse;
  currency: string;
  onSkip: () => void;
  onDelete: () => void;
  onUndo: () => void;
}) {
  const excluded = row.skipped || row.deleted;
  return (
    <tr className={excluded ? "is-skipped" : undefined}>
      <td>{formatDate(row.posted_on)}</td>
      <td>{row.description}</td>
      <td className="amount">{formatMoney(row.amount, row.currency || currency)}</td>
      <td>
        <div className="button-row">
          {excluded ? (
            <>
              <span className="badge badge--muted">{row.deleted ? "deleted" : "skipped"}</span>
              <button type="button" className="button--link" onClick={onUndo}>
                Undo
              </button>
            </>
          ) : (
            <>
              <button type="button" onClick={onSkip}>
                Skip
              </button>
              <button type="button" className="button--danger" onClick={onDelete}>
                Delete
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

function AddRowForm({
  onAdd,
  isPending,
}: {
  onAdd: (input: { posted_on: string; description: string; amount: string }) => void;
  isPending: boolean;
}) {
  const [postedOn, setPostedOn] = useState("");
  const [description, setDescription] = useState("");
  const [amount, setAmount] = useState("");

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    onAdd({ posted_on: postedOn, description, amount });
    setPostedOn("");
    setDescription("");
    setAmount("");
  }

  return (
    <form className="field-row" onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="row-date">Date</label>
        <input
          id="row-date"
          type="date"
          required
          value={postedOn}
          onChange={(event) => setPostedOn(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="row-description">Description</label>
        <input
          id="row-description"
          required
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="row-amount">Amount</label>
        <input
          id="row-amount"
          placeholder="-12.34"
          pattern="-?\d+\.\d{2}"
          required
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
        />
      </div>
      <button type="submit" disabled={isPending}>
        Add row
      </button>
    </form>
  );
}

// -- Receipt, screen 04c ------------------------------------------------------

function ReceiptPanel({ statement }: { statement: StatementResponse }) {
  const summaryQuery = useQuery({
    queryKey: ["statement-summary", statement.id],
    queryFn: async (): Promise<ImportSummary> => {
      const { data, response } = await api.GET("/api/v1/statements/{statement_id}/summary", {
        params: { path: { statement_id: statement.id } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  return (
    <section className="screen">
      <h1>Statement committed</h1>
      {summaryQuery.data ? (
        <div className="panel">
          <dl className="facts">
            <dt>Rows imported</dt>
            <dd>{summaryQuery.data.rows_imported}</dd>
            <dt>Duplicates removed</dt>
            <dd>{summaryQuery.data.duplicates_removed}</dd>
            <dt>Counted</dt>
            <dd>{summaryQuery.data.counted}</dd>
            <dt>Classified by rule</dt>
            <dd>{summaryQuery.data.classified_by_rule}</dd>
            <dt>Classified by you</dt>
            <dd>{summaryQuery.data.classified_by_user}</dd>
            <dt>Still unclassified</dt>
            <dd>{summaryQuery.data.unclassified}</dd>
          </dl>
        </div>
      ) : (
        <p className="muted">Loading the receipt…</p>
      )}

      <div className="button-row">
        <Link to={`/review/${statement.id}`} className="button button--primary">
          Review new merchants
        </Link>
        <Link to="/import" className="button">
          Import another statement
        </Link>
      </div>
    </section>
  );
}

// Re-exported so `isTerminal` stays a single source of truth for the polling
// hook's terminal-state definition, in case a future screen needs it too.
export { isTerminal };
