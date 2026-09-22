/**
 * Screen 07d: standing rule conflicts.
 *
 * Only two overlapping patterns are a defined conflict here (an open
 * question in CLAUDE.md — three or more isn't attempted), and "specific"
 * means the longer pattern.
 */

import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useResolveConflict, useRuleConflicts } from "./queries";

const ACTIONS = [
  { value: "specific_wins", label: "Specific rule wins" },
  { value: "narrow_broader", label: "Narrow the broader rule" },
  { value: "delete_specific", label: "Delete the more specific rule" },
] as const;

export function RuleConflicts() {
  const conflicts = useRuleConflicts();
  const resolve = useResolveConflict();
  const [applyToHistory, setApplyToHistory] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  // Set when arriving from a "Fix this conflict" link elsewhere (e.g. a rule
  // collision hit while classifying on the Review screen), so the specific
  // conflict that sent the person here is easy to find in what may be a
  // longer list rather than making them scan for it themselves.
  const [searchParams] = useSearchParams();
  const highlightId = searchParams.get("highlight");

  async function handleResolve(ruleId: string, action: (typeof ACTIONS)[number]["value"]) {
    setError(null);
    try {
      await resolve.mutateAsync({
        conflictId: ruleId,
        body: { action, apply_to_history: applyToHistory[ruleId] ?? false },
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not resolve the conflict.");
    }
  }

  if (conflicts.isLoading) return <p className="muted">Loading…</p>;
  if (conflicts.error instanceof ApiError) {
    return <p className="error">{conflicts.error.message}</p>;
  }

  return (
    <div className="stack">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {conflicts.data && conflicts.data.length === 0 && (
        <p className="muted">No rule conflicts right now.</p>
      )}

      <ul className="list">
        {conflicts.data?.map((conflict) => (
          <li
            key={conflict.rule_id}
            ref={(el) => {
              if (el && conflict.rule_id === highlightId) {
                el.scrollIntoView({ behavior: "smooth", block: "center" });
              }
            }}
            className={`list-item stack${conflict.rule_id === highlightId ? " list-item--highlighted" : ""}`}
          >
            <p>
              <code>{conflict.pattern}</code> overlaps another rule on{" "}
              <strong>{conflict.overlap_rows}</strong> row(s).
            </p>
            <div className="checkbox-field">
              <input
                id={`history-${conflict.rule_id}`}
                type="checkbox"
                checked={applyToHistory[conflict.rule_id] ?? false}
                onChange={(event) =>
                  setApplyToHistory((prev) => ({
                    ...prev,
                    [conflict.rule_id]: event.target.checked,
                  }))
                }
              />
              <label htmlFor={`history-${conflict.rule_id}`}>Also apply to past statements</label>
            </div>
            <div className="button-row">
              {ACTIONS.map((action) => (
                <button
                  key={action.value}
                  type="button"
                  onClick={() => void handleResolve(conflict.rule_id, action.value)}
                >
                  {action.label}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
