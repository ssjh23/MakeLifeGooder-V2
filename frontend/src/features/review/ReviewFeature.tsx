/**
 * Screens 04, 04b, 04c and 05: review and classify.
 *
 * Grouped by merchant, never by row. One decision here covers every row from
 * that merchant on the statement, which is what makes next month cheaper to
 * review than this one.
 */

import { useEffect, useRef, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  api,
  asRuleCollisionConflict,
  toApiError,
} from "../../api/client";
import type {
  CategoryResponse,
  DuplicatePair,
  ImportSummary,
  MatchTypeLiteral,
  MerchantGroup,
  ReviewBoard,
  StatementResponse,
} from "../../api/types";
import { formatMonth, formatMoney } from "../../shared/money";

export function ReviewFeature() {
  return (
    <Routes>
      <Route path="/" element={<StatementPicker />} />
      <Route path="/:statementId" element={<MerchantBoard />} />
    </Routes>
  );
}

// -- Picker ------------------------------------------------------------

function StatementPicker() {
  const [showReviewed, setShowReviewed] = useState(false);
  const query = useQuery({
    queryKey: ["statements", "ready"],
    queryFn: async (): Promise<StatementResponse[]> => {
      const { data, response } = await api.GET("/api/v1/statements", {
        params: { query: { status_filter: "ready" } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  // needs_review tracks the same condition that blocks "Finish review":
  // every counted row has a category. A statement stays "ready" forever
  // once committed, so without this every statement anyone has ever
  // finished reviewing would sit in this list next to the one that
  // actually needs a decision, with no way to tell them apart.
  const needsReview = (query.data ?? []).filter((s) => s.needs_review);
  const reviewed = (query.data ?? []).filter((s) => !s.needs_review);

  function renderStatement(statement: StatementResponse) {
    return (
      <li key={statement.id} className="list-item">
        <Link to={`/review/${statement.id}`}>
          <code>{statement.id}</code>
          {statement.statement_month && <> · {formatMonth(statement.statement_month)}</>}
        </Link>
        <span className="muted"> · uploaded {statement.uploaded_at.slice(0, 10)}</span>
      </li>
    );
  }

  return (
    <section className="screen">
      <h1>Review and classify</h1>
      <p className="muted">
        Statements that still need a category land here. Classification runs
        in the background after commit, so a statement you just imported may
        still show everything as new — refresh its board if it looks
        incomplete.
      </p>

      {query.isLoading && <p className="muted">Loading…</p>}
      {query.error instanceof ApiError && (
        <p className="error" role="alert">
          {query.error.message}
        </p>
      )}

      {query.data && needsReview.length === 0 && (
        <p className="muted">Nothing needs review right now.</p>
      )}

      {needsReview.length > 0 && <ul className="list">{needsReview.map(renderStatement)}</ul>}

      {reviewed.length > 0 && (
        <>
          <button
            type="button"
            className="button--link"
            onClick={() => setShowReviewed((v) => !v)}
          >
            {showReviewed ? "Hide" : "Show"} {reviewed.length} already-reviewed statement
            {reviewed.length === 1 ? "" : "s"}
          </button>
          {showReviewed && <ul className="list">{reviewed.map(renderStatement)}</ul>}
        </>
      )}
    </section>
  );
}

// -- Board ---------------------------------------------------------------

type Filter = "all" | "needs_category" | "new_merchants" | "overridden" | "duplicates";

// Mirrors the terminal-state pattern in ../../import/usePolling.ts: `undefined`
// covers "classification_status" being absent entirely (a statement fetched
// before its own commit response round-trips), not just its `null` value.
const CLASSIFICATION_TERMINAL: ReadonlySet<string | null | undefined> = new Set([
  "done",
  "failed",
  null,
  undefined,
]);
const CLASSIFICATION_POLL_INTERVAL_MS = 3000;

// Same rule-authoring options as the Rules screen's "New rule" form
// (RuleList.tsx) -- pattern, match type and ignore-case aren't locked to an
// exact match on this one descriptor just because the rule is being created
// from here instead of there.
const MATCH_TYPES: MatchTypeLiteral[] = ["contains", "starts_with", "ends_with", "exact"];

type ClassifyBody = {
  category_id?: string;
  new_category_name?: string;
  create_rule: boolean;
  scope: "future" | "backfill";
  pattern: string;
  match_type: MatchTypeLiteral;
  options: { ignore_case: boolean; match_negative: boolean };
};

// A plain string leaves an error like "a rule already covers this" a dead
// end. `conflictRuleId`, when present, is rendered as a direct link to the
// blocking rule instead of leaving the person to find it themselves.
//
// This links to the plain rules list (`/rules`), not `/rules/conflicts`:
// the conflict here is "creating your new rule would collide with this
// existing one", raised *before* the new rule is ever saved (RuleService
// checks, then aborts -- it never persists a rule it's about to reject).
// `/rules/conflicts` only ever lists overlaps between two rules that both
// already exist, so it would show nothing for this case -- there is no
// second rule. The one real row involved is `existing_id`, and the plain
// list is where a person can actually act on it (edit or delete).
//
// `retry`, when present, is the exact classify call that just failed --
// captured so the two inline recovery actions below (classify without a
// rule, or delete the blocking rule and try again) can resubmit it without
// making the person re-fill the form. Both reuse endpoints that already
// exist; there's no third "narrow the broader rule" action here, because
// that one edits an existing rule's pattern, which isn't a one-click choice
// -- that's still what the link is for.
type ActionError = {
  message: string;
  conflictRuleId?: string;
  retry?: {
    descriptorKey: string;
    body: ClassifyBody;
  };
};

function MerchantBoard() {
  const { statementId } = useParams<{ statementId: string }>();
  const id = statementId as string;
  const [filter, setFilter] = useState<Filter>("all");
  const [actionError, setActionError] = useState<ActionError | null>(null);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [confirmingRuleDelete, setConfirmingRuleDelete] = useState(false);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const boardKey = ["review-board", id];
  const board = useQuery({
    queryKey: boardKey,
    queryFn: async (): Promise<ReviewBoard> => {
      const { data, response } = await api.GET("/api/v1/statements/{statement_id}/review", {
        params: { path: { statement_id: id } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  const statement = useQuery({
    queryKey: ["statement", id],
    queryFn: async (): Promise<StatementResponse> => {
      const { data, response } = await api.GET("/api/v1/statements/{statement_id}", {
        params: { path: { statement_id: id } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    staleTime: 60_000,
    refetchInterval: (query) => {
      const status = query.state.data?.classification_status;
      return CLASSIFICATION_TERMINAL.has(status) ? false : CLASSIFICATION_POLL_INTERVAL_MS;
    },
    refetchOnWindowFocus: false,
  });

  // Classification finished while this screen was open: the merchant list a
  // person is already looking at is stale, so pull the fresh one in rather
  // than waiting for a manual refresh click.
  const previousClassificationStatus = useRef(statement.data?.classification_status);
  useEffect(() => {
    const previous = previousClassificationStatus.current;
    const current = statement.data?.classification_status;
    if (previous === "in_progress" && current === "done") {
      void queryClient.invalidateQueries({ queryKey: boardKey });
    }
    previousClassificationStatus.current = current;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statement.data?.classification_status]);

  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: async (): Promise<CategoryResponse[]> => {
      const { data, response } = await api.GET("/api/v1/categories", {});
      if (!data) throw await toApiError(response);
      return data;
    },
    staleTime: 60_000,
  });

  const currency = statement.data?.extraction?.currency ?? "SGD";

  function invalidateBoard() {
    void queryClient.invalidateQueries({ queryKey: boardKey });
    // Classifying a merchant, confirming rule matches, and resolving a
    // duplicate each move money into category_monthly_totals immediately
    // (ReviewService.classify/confirm_all/resolve_duplicate all call
    // AggregateRefresher before "Finish review" ever runs), so the
    // dashboard's cache needs the same invalidation the board query gets.
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  }

  async function classify(descriptorKey: string, body: ClassifyBody) {
    setActionError(null);
    setConfirmingRuleDelete(false);
    try {
      const { response } = await api.POST("/api/v1/review/merchants/{descriptor_key}/classify", {
        params: { path: { descriptor_key: descriptorKey } },
        body: {
          category_id: body.category_id ?? null,
          new_category_name: body.new_category_name ?? null,
          create_rule: body.create_rule,
          scope: body.scope,
          pattern: body.pattern,
          match_type: body.match_type,
          options: body.options,
        },
      });
      if (!response.ok) throw await toApiError(response);
      invalidateBoard();
    } catch (error) {
      if (error instanceof ApiError) {
        const collision = asRuleCollisionConflict(error);
        if (collision) {
          setActionError({
            message: collision.message,
            conflictRuleId: collision.existing_id,
            retry: { descriptorKey, body },
          });
          return;
        }
        setActionError({ message: error.message });
        return;
      }
      throw error;
    }
  }

  // The two conflict recoveries that don't require leaving this page. Both
  // just resubmit the classify call that just failed -- see ActionError's
  // `retry` field -- so neither needs the person to re-fill the form.

  async function classifyWithoutCreatingRule(retry: NonNullable<ActionError["retry"]>) {
    await classify(retry.descriptorKey, { ...retry.body, create_rule: false });
  }

  async function deleteBlockingRuleAndRetry(retry: NonNullable<ActionError["retry"]>, ruleId: string) {
    setActionError(null);
    try {
      const { response } = await api.DELETE("/api/v1/rules/{rule_id}", {
        params: { path: { rule_id: ruleId } },
      });
      if (!response.ok) throw await toApiError(response);
    } catch (error) {
      setActionError({ message: error instanceof ApiError ? error.message : "Could not delete the rule." });
      return;
    }
    await classify(retry.descriptorKey, retry.body);
  }

  async function splitDescriptor(descriptorKey: string, descriptionRaw: string) {
    setActionError(null);
    try {
      const { response } = await api.POST("/api/v1/review/merchants/{descriptor_key}/split", {
        params: { path: { descriptor_key: descriptorKey } },
        body: { description_raw: descriptionRaw },
      });
      if (!response.ok) throw await toApiError(response);
      invalidateBoard();
    } catch (error) {
      setActionError({
        message: error instanceof ApiError ? error.message : "Could not split this descriptor.",
      });
    }
  }

  async function mergeDescriptor(descriptorKey: string, targetDescriptorKey: string) {
    setActionError(null);
    try {
      const { response } = await api.POST("/api/v1/review/merchants/{descriptor_key}/merge", {
        params: { path: { descriptor_key: descriptorKey } },
        body: { target_descriptor_key: targetDescriptorKey },
      });
      if (!response.ok) throw await toApiError(response);
      invalidateBoard();
    } catch (error) {
      setActionError({
        message: error instanceof ApiError ? error.message : "Could not merge this merchant.",
      });
    }
  }

  async function confirmAll() {
    setActionError(null);
    try {
      const { response } = await api.POST(
        "/api/v1/statements/{statement_id}/review/confirm-all",
        {
          params: { path: { statement_id: id } },
          body: { descriptor_keys: null },
        },
      );
      if (!response.ok) throw await toApiError(response);
      invalidateBoard();
    } catch (error) {
      setActionError({ message: error instanceof ApiError ? error.message : "Could not confirm." });
    }
  }

  async function finish() {
    setActionError(null);
    try {
      const { data, response } = await api.POST("/api/v1/statements/{statement_id}/review/finish", {
        params: { path: { statement_id: id } },
      });
      if (!data) throw await toApiError(response);
      setSummary(data);
      // Finishing review is the point money actually lands in a category:
      // the dashboard's cached totals (global staleTime is 10s, so a quick
      // round trip serves them straight from cache otherwise) and the
      // picker's statement list both disagree with the server until this
      // fires, the same reasoning deleteStatement's onSuccess uses below.
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["statements", "ready"] });
      void queryClient.invalidateQueries({ queryKey: ["account"] });
    } catch (error) {
      setActionError({
        message: error instanceof ApiError ? error.message : "Could not finish review.",
      });
    }
  }

  const deleteStatement = useMutation({
    mutationFn: async () => {
      const { response } = await api.DELETE("/api/v1/statements/{statement_id}", {
        params: { path: { statement_id: id } },
        body: { confirm: true },
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: () => {
      // The statement is gone: its board, the picker's list, the dashboard
      // totals it touched, and account inventory counts all disagree with
      // the server otherwise.
      void queryClient.invalidateQueries({ queryKey: ["statements", "ready"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["account"] });
      navigate("/review", { replace: true });
    },
    onError: (error: unknown) => {
      setActionError({
        message: error instanceof ApiError ? error.message : "Could not delete this statement.",
      });
    },
  });

  if (board.isLoading) return <p className="muted">Loading…</p>;
  if (board.error instanceof ApiError) {
    return (
      <p className="error" role="alert">
        {board.error.message}
      </p>
    );
  }
  if (!board.data) return null;

  if (summary) {
    return (
      <section className="screen">
        <h1>Review finished</h1>
        <div className="panel">
          <dl className="facts">
            <dt>Rows imported</dt>
            <dd>{summary.rows_imported}</dd>
            <dt>Duplicates removed</dt>
            <dd>{summary.duplicates_removed}</dd>
            <dt>Counted</dt>
            <dd>{summary.counted}</dd>
            <dt>Classified by rule</dt>
            <dd>{summary.classified_by_rule}</dd>
            <dt>Classified by you</dt>
            <dd>{summary.classified_by_user}</dd>
            <dt>Still unclassified</dt>
            <dd>{summary.unclassified}</dd>
          </dl>
        </div>
        <div className="button-row">
          <Link to="/dashboard" className="button button--primary">
            Go to dashboard
          </Link>
        </div>
      </section>
    );
  }

  const { footer, filters, merchants } = board.data;
  const visible = merchants.filter((merchant) => {
    switch (filter) {
      case "needs_category":
        return !merchant.suggested_category && merchant.status !== "overridden";
      case "new_merchants":
        return merchant.status === "new";
      case "overridden":
        return merchant.status === "overridden";
      default:
        return true;
    }
  });

  return (
    <section className="screen">
      <div className="row">
        <h1>Review and classify</h1>
        <button
          type="button"
          className="button--danger"
          onClick={() => setConfirmingDelete((v) => !v)}
        >
          Delete this statement
        </button>
      </div>

      {confirmingDelete && (
        <div className="banner">
          <p>
            This permanently deletes this statement and every row on it, and
            recalculates every month it touched. This cannot be undone.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button--danger button--primary"
              disabled={deleteStatement.isPending}
              onClick={() => deleteStatement.mutate()}
            >
              {deleteStatement.isPending ? "Deleting…" : "Yes, delete this statement"}
            </button>
            <button
              type="button"
              className="button--link"
              onClick={() => setConfirmingDelete(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {statement.data?.classification_status === "in_progress" && (
        <div className="banner banner--locked">
          <span className="pulse-dot" aria-hidden="true" />
          Classification is still running in the background. New merchants
          will appear here as they&rsquo;re resolved — this list may be
          incomplete for a few moments after committing.
        </div>
      )}
      {statement.data?.classification_status === "failed" && (
        <div className="banner">
          Classification hit a problem and stopped. The rows on this
          statement are safe; refresh or check back shortly — this does not
          affect what&rsquo;s already been classified.
        </div>
      )}

      <div className="panel">
        <div className="row">
          <span>Statement total</span>
          <strong className="amount">{formatMoney(footer.statement_total, currency)}</strong>
        </div>
        <div className="row">
          <span>Classified</span>
          <span className="amount">{formatMoney(footer.classified, currency)}</span>
        </div>
        <div className="row">
          <span>Unassigned</span>
          <span className="amount">{formatMoney(footer.unassigned, currency)}</span>
        </div>
      </div>

      <div className="tabs">
        <button
          type="button"
          className={`tab${filter === "all" ? " is-active" : ""}`}
          onClick={() => setFilter("all")}
        >
          All ({merchants.length})
        </button>
        <button
          type="button"
          className={`tab${filter === "needs_category" ? " is-active" : ""}`}
          onClick={() => setFilter("needs_category")}
        >
          Needs category ({filters.needs_category})
        </button>
        <button
          type="button"
          className={`tab${filter === "new_merchants" ? " is-active" : ""}`}
          onClick={() => setFilter("new_merchants")}
        >
          New ({filters.new_merchants})
        </button>
        <button
          type="button"
          className={`tab${filter === "overridden" ? " is-active" : ""}`}
          onClick={() => setFilter("overridden")}
        >
          Overridden ({filters.overridden})
        </button>
        <button
          type="button"
          className={`tab${filter === "duplicates" ? " is-active" : ""}`}
          onClick={() => setFilter("duplicates")}
        >
          Duplicates ({filters.duplicates})
        </button>
      </div>

      {actionError && (
        <div className="error-panel">
          <p role="alert">{actionError.message}</p>
          {actionError.conflictRuleId && actionError.retry && (
            <>
              <div className="button-row">
                <button
                  type="button"
                  onClick={() => void classifyWithoutCreatingRule(actionError.retry!)}
                >
                  Classify without creating a rule
                </button>
                {confirmingRuleDelete ? (
                  <>
                    <button
                      type="button"
                      className="button--danger"
                      onClick={() =>
                        void deleteBlockingRuleAndRetry(actionError.retry!, actionError.conflictRuleId!)
                      }
                    >
                      Yes, delete it and create mine
                    </button>
                    <button type="button" className="button--link" onClick={() => setConfirmingRuleDelete(false)}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <button type="button" onClick={() => setConfirmingRuleDelete(true)}>
                    Delete the blocking rule and retry
                  </button>
                )}
              </div>
              <p className="muted">
                Or{" "}
                <Link to={`/rules?highlight=${actionError.conflictRuleId}`}>
                  edit the blocking rule
                </Link>{" "}
                instead, e.g. to narrow its pattern rather than remove it.
              </p>
            </>
          )}
        </div>
      )}

      {filter === "duplicates" ? (
        <DuplicatesPanel statementId={id} onResolved={invalidateBoard} />
      ) : (
        <ul className="list">
          {visible.map((merchant) => (
            <li key={merchant.descriptor_key} className="list-item">
              <MerchantRow
                merchant={merchant}
                otherMerchants={merchants.filter((m) => m.descriptor_key !== merchant.descriptor_key)}
                categories={categories.data ?? []}
                currency={currency}
                onClassify={(body) => classify(merchant.descriptor_key, body)}
                onSplit={(descriptionRaw) => splitDescriptor(merchant.descriptor_key, descriptionRaw)}
                onMerge={(targetKey) => mergeDescriptor(merchant.descriptor_key, targetKey)}
              />
            </li>
          ))}
        </ul>
      )}

      <div className="button-row">
        <button type="button" onClick={() => void confirmAll()}>
          Confirm all rule-matched
        </button>
        <button
          type="button"
          className="button--primary"
          disabled={!footer.can_finish}
          onClick={() => void finish()}
          title={footer.can_finish ? undefined : "Money is still unassigned"}
        >
          Finish review
        </button>
        <button type="button" className="button--link" onClick={invalidateBoard}>
          Refresh
        </button>
      </div>
    </section>
  );
}

function MerchantRow({
  merchant,
  otherMerchants,
  categories,
  currency,
  onClassify,
  onSplit,
  onMerge,
}: {
  merchant: MerchantGroup;
  otherMerchants: MerchantGroup[];
  categories: CategoryResponse[];
  currency: string;
  onClassify: (body: ClassifyBody) => void | Promise<void>;
  onSplit: (descriptionRaw: string) => void | Promise<void>;
  onMerge: (targetDescriptorKey: string) => void | Promise<void>;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const [categoryId, setCategoryId] = useState("");
  const [newCategoryName, setNewCategoryName] = useState("");
  const [createRule, setCreateRule] = useState(true);
  const [backfill, setBackfill] = useState(false);
  // Same rule-authoring fields as the Rules screen's own "New rule" form --
  // pattern defaults to this merchant's descriptor_key so the common case
  // (an exact match, today's only option before this) needs no typing.
  const [pattern, setPattern] = useState(merchant.descriptor_key);
  const [matchType, setMatchType] = useState<MatchTypeLiteral>("exact");
  const [ignoreCase, setIgnoreCase] = useState(true);
  const [confirmingSplit, setConfirmingSplit] = useState<string | null>(null);
  const [merging, setMerging] = useState(false);
  const [mergeTarget, setMergeTarget] = useState("");

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    void onClassify({
      category_id: categoryId === "__new__" ? undefined : categoryId || undefined,
      new_category_name: categoryId === "__new__" ? newCategoryName : undefined,
      create_rule: createRule,
      scope: backfill ? "backfill" : "future",
      pattern: pattern.trim() || merchant.descriptor_key,
      match_type: matchType,
      options: { ignore_case: ignoreCase, match_negative: false },
    });
  }

  async function handleMerge() {
    await onMerge(mergeTarget);
    setMerging(false);
    setMergeTarget("");
  }

  return (
    <div className="stack">
      <div className="row">
        <div>
          <strong>{merchant.display_name}</strong>{" "}
          <span className="badge badge--muted">{merchant.row_count} rows</span>{" "}
          {merchant.status === "overridden" && <span className="badge badge--accent">override</span>}
          {merchant.status === "rule_matched" && (
            <span className="badge badge--accent">rule matched</span>
          )}
          {merchant.classified_by && (
            <span className="badge badge--muted">{merchant.classified_by}</span>
          )}
        </div>
        <span className="amount">{formatMoney(merchant.total, currency)}</span>
      </div>
      {merchant.status === "rule_matched" && merchant.rule_id && (
        <p className="muted">
          Matched rule <code>{merchant.rule_pattern ?? merchant.rule_id}</code> ·{" "}
          <Link to="/rules">edit the rule</Link>
        </p>
      )}

      {otherMerchants.length > 0 && (
        <div className="button-row">
          <button type="button" className="button--link" onClick={() => setMerging((v) => !v)}>
            {merging ? "Cancel merge" : "Merge into…"}
          </button>
        </div>
      )}
      {merging && (
        <div className="field-row" style={{ alignItems: "flex-end" }}>
          <div className="field">
            <label htmlFor={`merge-target-${merchant.descriptor_key}`}>
              Move {merchant.row_count} transaction(s) into
            </label>
            <select
              id={`merge-target-${merchant.descriptor_key}`}
              value={mergeTarget}
              onChange={(event) => setMergeTarget(event.target.value)}
            >
              <option value="">Choose a merchant…</option>
              {otherMerchants.map((candidate) => (
                <option key={candidate.descriptor_key} value={candidate.descriptor_key}>
                  {candidate.display_name}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="button--danger"
            disabled={!mergeTarget}
            onClick={() => void handleMerge()}
          >
            Merge and remove "{merchant.display_name}"
          </button>
          <button type="button" onClick={() => setMerging(false)}>
            Cancel
          </button>
        </div>
      )}
      {merging && (
        <p className="muted">
          Every past and future transaction reading exactly like one of the lines below will be
          treated as the merchant you merge into instead — including on statements you&apos;ve
          already finished reviewing. They&apos;ll immediately take on that merchant&apos;s
          category.
        </p>
      )}

      <button type="button" className="button--link" onClick={() => setShowEvidence((v) => !v)}>
        {showEvidence ? "Hide" : "Show"} the {merchant.raw_descriptors.length} descriptor(s) grouped here
      </button>
      {showEvidence && (
        <ul className="list">
          {merchant.raw_descriptors.map((descriptor) => (
            <li key={descriptor} className="row">
              <code>{descriptor}</code>
              {merchant.raw_descriptors.length > 1 &&
                (confirmingSplit === descriptor ? (
                  <span className="button-row">
                    <button
                      type="button"
                      className="button--danger"
                      onClick={() => {
                        setConfirmingSplit(null);
                        void onSplit(descriptor);
                      }}
                    >
                      Yes, split it off
                    </button>
                    <button
                      type="button"
                      className="button--link"
                      onClick={() => setConfirmingSplit(null)}
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="button--link"
                    onClick={() => setConfirmingSplit(descriptor)}
                  >
                    Split off
                  </button>
                ))}
            </li>
          ))}
          {confirmingSplit && (
            <p className="muted">
              This treats every past and future transaction that reads exactly &quot;{confirmingSplit}
              &quot; as a different merchant — including ones on statements you&apos;ve already
              finished reviewing. They&apos;ll become unclassified again and appear as a new merchant
              to classify.
            </p>
          )}
        </ul>
      )}

      {merchant.suggested_category && !categoryId && (
        <p className="muted">Suggested: {merchant.suggested_category.name}</p>
      )}

      <form className="form" onSubmit={handleSubmit}>
        <div className="field-row">
          <div className="field">
            <label htmlFor={`category-${merchant.descriptor_key}`}>Category</label>
            <select
              id={`category-${merchant.descriptor_key}`}
              value={categoryId}
              onChange={(event) => setCategoryId(event.target.value)}
            >
              <option value="">
                {merchant.suggested_category
                  ? `Use suggestion: ${merchant.suggested_category.name}`
                  : "Choose a category"}
              </option>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
              <option value="__new__">+ New category</option>
            </select>
          </div>
          {categoryId === "__new__" && (
            <div className="field">
              <label htmlFor={`new-category-${merchant.descriptor_key}`}>New category name</label>
              <input
                id={`new-category-${merchant.descriptor_key}`}
                value={newCategoryName}
                onChange={(event) => setNewCategoryName(event.target.value)}
                required
              />
            </div>
          )}
        </div>

        <div className="checkbox-field">
          <input
            id={`rule-${merchant.descriptor_key}`}
            type="checkbox"
            checked={createRule}
            onChange={(event) => setCreateRule(event.target.checked)}
          />
          <label htmlFor={`rule-${merchant.descriptor_key}`}>
            Remember this for future statements
          </label>
        </div>

        {createRule && (
          <>
            <div className="field-row">
              <div className="field">
                <label htmlFor={`pattern-${merchant.descriptor_key}`}>Pattern</label>
                <input
                  id={`pattern-${merchant.descriptor_key}`}
                  value={pattern}
                  onChange={(event) => setPattern(event.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor={`match-type-${merchant.descriptor_key}`}>Match type</label>
                <select
                  id={`match-type-${merchant.descriptor_key}`}
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
            </div>
            <div className="checkbox-field">
              <input
                id={`ignore-case-${merchant.descriptor_key}`}
                type="checkbox"
                checked={ignoreCase}
                onChange={(event) => setIgnoreCase(event.target.checked)}
              />
              <label htmlFor={`ignore-case-${merchant.descriptor_key}`}>Ignore case</label>
            </div>
          </>
        )}

        <div className="checkbox-field">
          <input
            id={`backfill-${merchant.descriptor_key}`}
            type="checkbox"
            checked={backfill}
            onChange={(event) => setBackfill(event.target.checked)}
          />
          <label htmlFor={`backfill-${merchant.descriptor_key}`}>
            Also apply to past statements (changes past totals)
          </label>
        </div>

        <div className="button-row">
          <button
            type="submit"
            className="button--primary"
            disabled={
              !merchant.suggested_category &&
              !categoryId &&
              !(categoryId === "__new__" && newCategoryName)
            }
          >
            {merchant.suggested_category && !categoryId ? "Confirm suggestion" : "Classify"}
          </button>
        </div>
      </form>
    </div>
  );
}

function DuplicatesPanel({
  statementId,
  onResolved,
}: {
  statementId: string;
  onResolved: () => void;
}) {
  const queryClient = useQueryClient();
  const [resolved, setResolved] = useState<Record<string, "kept" | "removed">>({});
  const [error, setError] = useState<string | null>(null);
  const key = ["review-duplicates", statementId];

  const query = useQuery({
    queryKey: key,
    queryFn: async (): Promise<DuplicatePair[]> => {
      const { data, response } = await api.GET(
        "/api/v1/statements/{statement_id}/review/duplicates",
        { params: { path: { statement_id: statementId } } },
      );
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  async function resolve(pair: DuplicatePair, action: "keep_both" | "remove", removeRowId?: string) {
    setError(null);
    try {
      const { response } = await api.POST(
        "/api/v1/statements/{statement_id}/duplicates/{pair_id}/resolve",
        {
          params: { path: { statement_id: statementId, pair_id: pair.pair_id } },
          body: { action, remove_row_id: removeRowId ?? null },
        },
      );
      if (!response.ok) throw await toApiError(response);
      setResolved((prev) => ({ ...prev, [pair.pair_id]: action === "remove" ? "removed" : "kept" }));
      onResolved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not resolve this duplicate.");
    }
  }

  async function undo(pair: DuplicatePair) {
    setError(null);
    try {
      const { response } = await api.POST(
        "/api/v1/statements/{statement_id}/duplicates/{pair_id}/undo",
        { params: { path: { statement_id: statementId, pair_id: pair.pair_id } } },
      );
      if (!response.ok) throw await toApiError(response);
      setResolved((prev) => {
        const next = { ...prev };
        delete next[pair.pair_id];
        return next;
      });
      void queryClient.invalidateQueries({ queryKey: key });
      onResolved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not undo.");
    }
  }

  if (query.isLoading) return <p className="muted">Loading duplicates…</p>;
  if (!query.data || query.data.length === 0) {
    return <p className="muted">No duplicate rows flagged on this statement.</p>;
  }

  return (
    <ul className="list">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {query.data.map((pair) => {
        const outcome = resolved[pair.pair_id];
        return (
          <li key={pair.pair_id} className="list-item">
            <div className="row">
              <span>
                {pair.merchant} — {pair.hours_apart.toFixed(1)}h apart
              </span>
              <span className="amount">{formatMoney(pair.amount, "SGD")}</span>
            </div>
            {outcome ? (
              <div className="row">
                <span className="badge badge--muted">{outcome}</span>
                <button type="button" className="button--link" onClick={() => void undo(pair)}>
                  Undo
                </button>
              </div>
            ) : (
              <div className="button-row">
                <button type="button" onClick={() => void resolve(pair, "keep_both")}>
                  Keep both
                </button>
                {pair.row_ids.map((rowId) => (
                  <button
                    key={rowId}
                    type="button"
                    className="button--danger"
                    onClick={() => void resolve(pair, "remove", rowId)}
                  >
                    Remove row {rowId.slice(0, 8)}
                  </button>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
