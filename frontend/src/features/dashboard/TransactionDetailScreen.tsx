/**
 * Screen 06c: the only place that answers "why is this here".
 *
 * `provenance.classified_by` names the cascade rung that put this row in its
 * category. Overriding replaces that for this row only — it never touches
 * the statement total, and the response echoes both so the client doesn't
 * have to trust that invariant blindly.
 */

import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, toApiError } from "../../api/client";
import { formatDate, formatMoney } from "../../shared/money";

const PROVENANCE_LABEL: Record<string, string> = {
  override: "You classified this by hand.",
  alias: "Matched an existing merchant alias.",
  merchant_default: "Matched a merchant by similarity.",
  llm: "Classified by the model.",
};

const FLAG_LABEL: Record<string, string> = {
  paired_with_duplicate_kept: "Paired with a similar transaction — you kept both.",
};

type OverrideScope = "row" | "company";

export function TransactionDetailScreen() {
  const { transactionId } = useParams<{ transactionId: string }>();
  const queryClient = useQueryClient();
  const [categoryId, setCategoryId] = useState("");
  const [overrideScope, setOverrideScope] = useState<OverrideScope>("row");
  const [overrideError, setOverrideError] = useState<string | null>(null);

  const transactionQuery = useQuery({
    queryKey: ["transaction", transactionId],
    enabled: transactionId != null,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/transactions/{transaction_id}", {
        params: { path: { transaction_id: transactionId as string } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  const categoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/categories", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  const override = useMutation({
    mutationFn: async (newCategoryId: string) => {
      const { data, response } = await api.POST("/api/v1/transactions/{transaction_id}/override", {
        params: { path: { transaction_id: transactionId as string } },
        body: { category_id: newCategoryId },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["transaction", transactionId] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  // "Every row from this company" reaches the same cascade the review board
  // uses (ReviewService.classify, scope=backfill) rather than the single-row
  // override endpoint: this is the one path that restates every historical
  // row sharing this descriptor, not just the one open here.
  const backfillCompany = useMutation({
    mutationFn: async (input: { descriptorKey: string; newCategoryId: string }) => {
      const { response } = await api.POST("/api/v1/review/merchants/{descriptor_key}/classify", {
        params: { path: { descriptor_key: input.descriptorKey } },
        body: {
          category_id: input.newCategoryId,
          new_category_name: null,
          create_rule: false,
          scope: "backfill",
          // Unused server-side when create_rule is false, but required by
          // the request shape now that classify() takes the same
          // rule-authoring options the Rules screen does.
          match_type: "exact",
          options: { ignore_case: true, match_negative: false },
        },
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["transaction", transactionId] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
  });

  async function handleOverride(event: React.FormEvent) {
    event.preventDefault();
    if (!categoryId) return;
    setOverrideError(null);
    try {
      if (overrideScope === "company" && transaction?.descriptor_key) {
        await backfillCompany.mutateAsync({
          descriptorKey: transaction.descriptor_key,
          newCategoryId: categoryId,
        });
      } else {
        await override.mutateAsync(categoryId);
      }
    } catch (err) {
      setOverrideError(err instanceof ApiError ? err.message : "Could not reclassify this row.");
    }
  }

  if (transactionQuery.isLoading) return <section className="screen">Loading…</section>;

  if (transactionQuery.error) {
    return (
      <section className="screen">
        <p className="error" role="alert">
          {transactionQuery.error instanceof ApiError
            ? transactionQuery.error.message
            : "Could not load this transaction."}
        </p>
      </section>
    );
  }

  const transaction = transactionQuery.data;
  if (!transaction) return null;

  const provenance = transaction.provenance;
  const classifiedBy = provenance?.classified_by ?? undefined;
  // A row classified purely by a rule, or by a manual override with no
  // merchant ever resolved (merchant_id null either way), has nothing a
  // Merchant row can name -- the rule's own pattern, or failing that the
  // descriptor_key the override applies to, is the only thing that does.
  const companyLabel = transaction.merchant_name ?? provenance?.rule_pattern ?? transaction.descriptor_key;

  return (
    <section className="screen">
      <p className="muted">
        <Link to="/dashboard">← Dashboard</Link>
      </p>
      <h1>{transaction.description}</h1>

      <dl className="facts">
        <dt>Date</dt>
        <dd>{formatDate(transaction.posted_on)}</dd>
        <dt>Amount</dt>
        <dd className="amount">{formatMoney(transaction.amount, transaction.currency)}</dd>
        {companyLabel && (
          <>
            <dt>Company</dt>
            <dd>
              {companyLabel}{" "}
              <Link to={`/dashboard?search=${encodeURIComponent(companyLabel)}`}>
                See other rows
              </Link>
            </dd>
          </>
        )}
        {transaction.flags.length > 0 && (
          <>
            <dt>Flags</dt>
            <dd>
              {transaction.flags.map((flag) => (
                <span
                  key={flag}
                  className="badge badge--muted"
                  style={{ marginRight: 6, display: "block" }}
                >
                  {FLAG_LABEL[flag] ?? flag}
                </span>
              ))}
            </dd>
          </>
        )}
      </dl>

      <div className="panel">
        <h2>Why this category</h2>
        {provenance ? (
          <>
            <p>{classifiedBy ? PROVENANCE_LABEL[classifiedBy] ?? classifiedBy : "Not yet classified."}</p>
            {classifiedBy === "llm" && provenance.prompt_version && (
              <p className="muted">Prompt version {provenance.prompt_version}.</p>
            )}
            {provenance.rule_id && (
              <p className="muted">
                Matched rule <code>{provenance.rule_pattern ?? provenance.rule_id}</code> ·{" "}
                <Link to="/rules">edit the rule</Link>
              </p>
            )}
            <p className="muted">
              From{" "}
              <Link to={`/import/${provenance.statement_id}`}>this statement</Link>
              {provenance.page != null && ` — page ${provenance.page}`}
              {provenance.line != null && `, line ${provenance.line}`}.
            </p>
          </>
        ) : (
          <p className="muted">No provenance recorded for this row.</p>
        )}
      </div>

      <div className="panel">
        <h2>If the category is wrong</h2>
        <p className="muted">
          Changing a category moves money between categories. It never changes
          a statement total.
        </p>
        <form className="form" onSubmit={handleOverride}>
          <div className="field">
            <label htmlFor="category">Category</label>
            <select
              id="category"
              value={categoryId}
              onChange={(event) => setCategoryId(event.target.value)}
              required
            >
              <option value="" disabled>
                Choose a category
              </option>
              {categoriesQuery.data?.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <div className="checkbox-field">
              <input
                type="radio"
                id="scope-row"
                name="override-scope"
                checked={overrideScope === "row"}
                onChange={() => setOverrideScope("row")}
              />
              <label htmlFor="scope-row">
                This row only — marked as overridden by you, left alone by future rule runs
              </label>
            </div>
            <div className="checkbox-field">
              <input
                type="radio"
                id="scope-company"
                name="override-scope"
                checked={overrideScope === "company"}
                disabled={!transaction.descriptor_key}
                onChange={() => setOverrideScope("company")}
              />
              <label htmlFor="scope-company">
                Every row from this company — reclassifies every historical row with this
                descriptor
              </label>
            </div>
          </div>

          {overrideError && (
            <p className="error" role="alert">
              {overrideError}
            </p>
          )}
          {(override.isSuccess || backfillCompany.isSuccess) && !overrideError && (
            <p className="muted">Reclassified.</p>
          )}
          <div className="button-row">
            <Link to="/dashboard" className="button">
              Close
            </Link>
            <button
              type="submit"
              className="button--primary"
              disabled={override.isPending || backfillCompany.isPending}
            >
              {override.isPending || backfillCompany.isPending ? "Saving…" : "Save override"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
