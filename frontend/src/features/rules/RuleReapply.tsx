/**
 * Screen 07e: a preview screen with a rerun button, not a button that
 * reruns. Reapply only becomes the obvious next step after a preview has
 * been run for the current month selection.
 */

import { useState } from "react";
import { ApiError } from "../../api/client";
import { formatMoney, formatMonth } from "../../shared/money";
import { useReapply, useReapplyPreview } from "./queries";

export function RuleReapply() {
  const [monthsInput, setMonthsInput] = useState("");
  const [keepOverrides, setKeepOverrides] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);
  const preview = useReapplyPreview();
  const reapply = useReapply();

  function parsedMonths(): string[] | undefined {
    const months = monthsInput
      .split(",")
      .map((m) => m.trim())
      .filter(Boolean);
    return months.length > 0 ? months : undefined;
  }

  async function handlePreview() {
    setError(null);
    setQueued(false);
    try {
      await preview.mutateAsync({ months: parsedMonths() });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not build a preview.");
    }
  }

  async function handleReapply() {
    setError(null);
    try {
      await reapply.mutateAsync({ keep_overrides: keepOverrides, months: parsedMonths() });
      setQueued(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not queue the reapply.");
    }
  }

  const result = preview.data;

  return (
    <div className="stack">
      <div className="panel form">
        <div className="field">
          <label htmlFor="months">Months (optional)</label>
          <input
            id="months"
            placeholder="2025-01, 2025-02"
            value={monthsInput}
            onChange={(event) => setMonthsInput(event.target.value)}
          />
          <p className="field-hint">Comma-separated "YYYY-MM". Leave blank for every imported month.</p>
        </div>

        <div className="button-row">
          <button type="button" onClick={() => void handlePreview()} disabled={preview.isPending}>
            {preview.isPending ? "Building preview…" : "Preview"}
          </button>
        </div>
      </div>

      {result && (
        <div className="panel stack">
          <h2>What would change</h2>
          {result.overrides_at_risk > 0 && (
            <p className="error">
              {result.overrides_at_risk} manual override(s) are at risk if you uncheck "keep
              overrides" below.
            </p>
          )}
          <table className="table">
            <thead>
              <tr>
                <th>Month</th>
                <th>Rows retested</th>
                <th>Rows changing</th>
                <th>Effect on totals</th>
              </tr>
            </thead>
            <tbody>
              {result.per_month.map((month) => (
                <tr key={month.month}>
                  <td>{formatMonth(month.month)}</td>
                  <td>{month.rows_retested}</td>
                  <td>{month.rows_changing}</td>
                  <td className="amount">{formatMoney(month.effect_on_totals, "SGD")}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="checkbox-field">
            <input
              id="keep-overrides"
              type="checkbox"
              checked={keepOverrides}
              onChange={(event) => setKeepOverrides(event.target.checked)}
            />
            <label htmlFor="keep-overrides">
              Keep manual overrides (unchecking discards them irrecoverably)
            </label>
          </div>

          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}

          {queued ? (
            <p className="muted">Reapply queued. It runs in the background.</p>
          ) : (
            <div className="button-row">
              <button
                type="button"
                className={keepOverrides ? "button--primary" : "button--danger button--primary"}
                onClick={() => void handleReapply()}
                disabled={reapply.isPending}
              >
                Reapply rules
              </button>
            </div>
          )}
        </div>
      )}

      {!result && error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
