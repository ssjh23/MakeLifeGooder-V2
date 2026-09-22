/**
 * Screens 07 to 07c: the rule list and the new-rule form with a live preview.
 */

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, asRuleCollisionConflict } from "../../api/client";
import type { MatchTypeLiteral, RuleResponse } from "../../api/types";
import {
  useCategories,
  useCreateRule,
  useDeleteRule,
  usePreviewRule,
  useRules,
  useUpdateRule,
} from "./queries";

const MATCH_TYPES: MatchTypeLiteral[] = ["contains", "starts_with", "ends_with", "exact"];

export function RuleList() {
  const rules = useRules();
  const categories = useCategories();
  const deleteRule = useDeleteRule();
  const [editingId, setEditingId] = useState<string | null>(null);
  // Set either from a `?highlight=` link (e.g. a rule collision hit while
  // classifying on the Review screen) or from this page's own "New rule"
  // form hitting the same collision -- both name the one rule that's
  // actually blocking them, and this is where a person can edit or delete
  // it, since that rule is real and this rule *would* be but never got
  // created (RuleService checks for a conflict before it ever saves one).
  const [searchParams] = useSearchParams();
  const [highlightId, setHighlightId] = useState<string | null>(searchParams.get("highlight"));

  return (
    <div className="stack">
      <NewRuleForm onConflict={setHighlightId} />

      <div className="panel">
        <h2>Existing rules</h2>
        {rules.isLoading && <p className="muted">Loading…</p>}
        {rules.error instanceof ApiError && <p className="error">{rules.error.message}</p>}
        {rules.data && rules.data.length === 0 && (
          <p className="muted">No rules yet. Create one above.</p>
        )}
        {rules.data && rules.data.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Pattern</th>
                <th>Match</th>
                <th>Category</th>
                <th>Rows matched</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rules.data.map((rule) =>
                editingId === rule.id ? (
                  <EditRuleRow
                    key={rule.id}
                    rule={rule}
                    onDone={() => setEditingId(null)}
                  />
                ) : (
                  <tr
                    key={rule.id}
                    ref={(el) => {
                      if (el && rule.id === highlightId) {
                        el.scrollIntoView({ behavior: "smooth", block: "center" });
                      }
                    }}
                    className={rule.id === highlightId ? "list-item--highlighted" : undefined}
                  >
                    <td>
                      <code>{rule.pattern}</code>
                    </td>
                    <td>
                      <span className="badge badge--muted">{rule.match_type}</span>
                    </td>
                    <td>{rule.category_name}</td>
                    <td>{rule.rows_matched}</td>
                    <td className="button-row">
                      <button type="button" onClick={() => setEditingId(rule.id)}>
                        Edit
                      </button>
                      <button
                        type="button"
                        className="button--danger"
                        onClick={() => void deleteRule.mutateAsync(rule.id)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        )}
      </div>

      {categories.error instanceof ApiError && (
        <p className="error">Could not load categories: {categories.error.message}</p>
      )}
    </div>
  );
}

function EditRuleRow({ rule, onDone }: { rule: RuleResponse; onDone: () => void }) {
  const categories = useCategories();
  const updateRule = useUpdateRule();
  const [pattern, setPattern] = useState(rule.pattern);
  const [matchType, setMatchType] = useState<MatchTypeLiteral>(rule.match_type);
  const [categoryId, setCategoryId] = useState(rule.category_id);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setError(null);
    try {
      await updateRule.mutateAsync({
        ruleId: rule.id,
        body: { pattern, match_type: matchType, category_id: categoryId },
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the rule.");
    }
  }

  return (
    <tr>
      <td>
        <input value={pattern} onChange={(event) => setPattern(event.target.value)} />
      </td>
      <td>
        <select
          value={matchType}
          onChange={(event) => setMatchType(event.target.value as MatchTypeLiteral)}
        >
          {MATCH_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </td>
      <td>
        <select value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>
          {categories.data?.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </select>
      </td>
      <td colSpan={1}>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </td>
      <td className="button-row">
        <button type="button" className="button--primary" onClick={() => void handleSave()}>
          Save
        </button>
        <button type="button" onClick={onDone}>
          Cancel
        </button>
      </td>
    </tr>
  );
}

function NewRuleForm({ onConflict }: { onConflict: (ruleId: string) => void }) {
  const categories = useCategories();
  const preview = usePreviewRule();
  const createRule = useCreateRule();

  const [pattern, setPattern] = useState("");
  const [matchType, setMatchType] = useState<MatchTypeLiteral>("contains");
  const [categoryId, setCategoryId] = useState("");
  const [scope, setScope] = useState<"future" | "backfill">("future");
  const [ignoreCase, setIgnoreCase] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);

  // Live preview, debounced: the pattern can contain characters a query
  // string would mangle, which is why this is a POST rather than a GET fired
  // on every keystroke without debouncing.
  useEffect(() => {
    if (!pattern.trim()) {
      preview.reset();
      return;
    }
    const handle = window.setTimeout(() => {
      preview.mutate({
        pattern,
        match_type: matchType,
        options: { ignore_case: ignoreCase, match_negative: false },
      });
    }, 300);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pattern, matchType, ignoreCase]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setConflictMessage(null);
    try {
      await createRule.mutateAsync({
        pattern,
        match_type: matchType,
        category_id: categoryId,
        scope,
        options: { ignore_case: ignoreCase, match_negative: false },
      });
      setPattern("");
      setCategoryId("");
      setScope("future");
      preview.reset();
    } catch (err) {
      if (err instanceof ApiError) {
        const conflict = asRuleCollisionConflict(err);
        if (conflict) {
          setConflictMessage(conflict.message);
          onConflict(conflict.existing_id);
          return;
        }
        setError(err.message);
      } else {
        setError("Could not create the rule.");
      }
    }
  }

  const previewResult = preview.data;

  return (
    <form className="panel form" onSubmit={handleSubmit}>
      <h2>New rule</h2>

      <div className="field">
        <label htmlFor="pattern">Pattern</label>
        <input
          id="pattern"
          required
          value={pattern}
          onChange={(event) => setPattern(event.target.value)}
        />
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="match-type">Match type</label>
          <select
            id="match-type"
            value={matchType}
            onChange={(event) => setMatchType(event.target.value as MatchTypeLiteral)}
          >
            {MATCH_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="category">Category</label>
          <select
            id="category"
            required
            value={categoryId}
            onChange={(event) => setCategoryId(event.target.value)}
          >
            <option value="" disabled>
              Choose a category
            </option>
            {categories.data?.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="checkbox-field">
        <input
          id="ignore-case"
          type="checkbox"
          checked={ignoreCase}
          onChange={(event) => setIgnoreCase(event.target.checked)}
        />
        <label htmlFor="ignore-case">Ignore case</label>
      </div>

      <div className="checkbox-field">
        <input
          id="backfill"
          type="checkbox"
          checked={scope === "backfill"}
          onChange={(event) => setScope(event.target.checked ? "backfill" : "future")}
        />
        <label htmlFor="backfill">Also apply to past statements (backfill)</label>
      </div>

      {previewResult && (
        <div className="banner">
          <p>
            Matches <strong>{previewResult.matches}</strong> row(s). {previewResult.already_in_category}{" "}
            already in this category.
          </p>
          {previewResult.would_relabel_manual > 0 && (
            <p className="error">
              Would relabel {previewResult.would_relabel_manual} row(s) you classified by hand.
            </p>
          )}
          {previewResult.conflicts.length > 0 && (
            <p className="muted">
              Overlaps {previewResult.conflicts.length} existing rule(s), e.g.{" "}
              <code>{previewResult.conflicts[0]?.pattern}</code> (
              {previewResult.conflicts[0]?.overlap_rows} shared rows).
            </p>
          )}
        </div>
      )}

      {conflictMessage && (
        <div className="banner">
          <p className="error">{conflictMessage}</p>
          <p className="muted">The blocking rule is highlighted below — edit or delete it, then try again.</p>
        </div>
      )}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="button-row">
        <button type="submit" className="button--primary" disabled={createRule.isPending}>
          Create rule
        </button>
      </div>
    </form>
  );
}
