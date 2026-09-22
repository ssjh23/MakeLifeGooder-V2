/**
 * Screen 08b: request a data export.
 *
 * Columns, row count and estimated size are declared by the server before
 * the file is written (`ExportResponse`), so this shows that manifest before
 * offering the download rather than surprising the user with the file itself.
 */

import { useState } from "react";
import { ApiError } from "../../api/client";
import type { ExportRequest } from "../../api/types";
import { useCards, useCategories, useExportStatus, useStartExport } from "./queries";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

export function ExportsPanel() {
  const [scope, setScope] = useState<ExportRequest["scope"]>("all");
  const [scopeId, setScopeId] = useState("");
  const [grouping, setGrouping] = useState<ExportRequest["grouping"]>("by_month");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [exportId, setExportId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const categories = useCategories();
  const cards = useCards();
  const start = useStartExport();
  const status = useExportStatus(exportId);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await start.mutateAsync({
        scope,
        scope_id: scope === "all" ? null : scopeId || null,
        period: from && to ? { from, to } : null,
        format: "csv",
        grouping,
      });
      setExportId(result.export_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the export.");
    }
  }

  return (
    <div className="stack">
      <form className="form" onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="export-scope">What to export</label>
          <select
            id="export-scope"
            value={scope}
            onChange={(event) => {
              setScope(event.target.value as ExportRequest["scope"]);
              setScopeId("");
            }}
          >
            <option value="all">Everything</option>
            <option value="category">One category</option>
            <option value="card">One card</option>
            <option value="statement">One statement</option>
          </select>
        </div>

        {scope === "category" && (
          <div className="field">
            <label htmlFor="export-scope-category">Category</label>
            <select
              id="export-scope-category"
              required
              value={scopeId}
              onChange={(event) => setScopeId(event.target.value)}
            >
              <option value="">Choose a category…</option>
              {(categories.data ?? []).map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
            </select>
          </div>
        )}

        {scope === "card" && (
          <div className="field">
            <label htmlFor="export-scope-card">Card</label>
            <select
              id="export-scope-card"
              required
              value={scopeId}
              onChange={(event) => setScopeId(event.target.value)}
            >
              <option value="">Choose a card…</option>
              {(cards.data ?? []).map((card) => (
                <option key={card.id} value={card.id}>
                  {card.nickname}
                </option>
              ))}
            </select>
          </div>
        )}

        {scope === "statement" && (
          <div className="field">
            <label htmlFor="export-scope-statement">Statement id</label>
            <input
              id="export-scope-statement"
              required
              value={scopeId}
              onChange={(event) => setScopeId(event.target.value)}
              placeholder="Paste a statement id"
            />
          </div>
        )}

        <div className="field-row">
          <div className="field">
            <label htmlFor="export-from">From (optional)</label>
            <input
              id="export-from"
              type="date"
              value={from}
              onChange={(event) => setFrom(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="export-to">To (optional)</label>
            <input
              id="export-to"
              type="date"
              value={to}
              onChange={(event) => setTo(event.target.value)}
            />
          </div>
        </div>

        <div className="field">
          <label htmlFor="export-grouping">Grouping</label>
          <select
            id="export-grouping"
            value={grouping}
            onChange={(event) => setGrouping(event.target.value as ExportRequest["grouping"])}
          >
            <option value="by_month">By month</option>
            <option value="by_category">By category</option>
            <option value="flat">Flat</option>
          </select>
        </div>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        <div className="button-row">
          <button type="submit" className="button--primary" disabled={start.isPending}>
            {start.isPending ? "Starting…" : "Start export"}
          </button>
        </div>
      </form>

      {exportId && status.data && (
        <div className="panel">
          <h2>Export {status.data.status}</h2>
          <dl className="facts">
            <dt>File</dt>
            <dd>{status.data.filename}</dd>
            <dt>Columns</dt>
            <dd>{status.data.columns.join(", ")}</dd>
            <dt>Rows</dt>
            <dd>{status.data.row_count}</dd>
            <dt>Estimated size</dt>
            <dd>{formatBytes(status.data.estimated_size_bytes)}</dd>
          </dl>
          {status.data.status === "ready" && status.data.download_url && (
            <p>
              <a className="button button--primary" href={status.data.download_url}>
                Download
              </a>
            </p>
          )}
          {status.data.status === "failed" && (
            <p className="error" role="alert">
              The export failed. Try again.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
