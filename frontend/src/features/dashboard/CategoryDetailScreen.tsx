/**
 * Screen 06b: category detail. Who is spending this category's money.
 */

import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, toApiError } from "../../api/client";
import { formatDate, formatMoney, formatMonth } from "../../shared/money";

export function CategoryDetailScreen() {
  const { categoryId } = useParams<{ categoryId: string }>();

  const detailQuery = useQuery({
    queryKey: ["dashboard", "category", categoryId],
    enabled: categoryId != null,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/dashboard/categories/{category_id}", {
        params: { path: { category_id: categoryId as string } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  const transactionsQuery = useQuery({
    queryKey: ["transactions", { categoryId }],
    enabled: categoryId != null,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/transactions", {
        params: { query: { category_id: categoryId as string } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  if (detailQuery.isLoading) return <section className="screen">Loading…</section>;

  if (detailQuery.error) {
    return (
      <section className="screen">
        <p className="error" role="alert">
          {detailQuery.error instanceof ApiError
            ? detailQuery.error.message
            : "Could not load this category."}
        </p>
      </section>
    );
  }

  const detail = detailQuery.data;
  if (!detail) return null;

  const currency = "SGD";

  return (
    <section className="screen">
      <p className="muted">
        <Link to="/dashboard">← Dashboard</Link>
      </p>
      <h1>{detail.name}</h1>

      <div className="stack">
        <div className="panel">
          <h2>Last six months</h2>
          {detail.six_month_totals.length === 0 && (
            <p className="muted">No history for this category yet.</p>
          )}
          {detail.six_month_totals.length > 0 && (
            <>
              {(() => {
                const totals = detail.six_month_totals;
                const currentMonth = totals.at(-1);
                const max = Math.max(1, ...totals.map((m) => Math.abs(Number(m.total))));
                const highest = totals.reduce((a, b) =>
                  Math.abs(Number(b.total)) > Math.abs(Number(a.total)) ? b : a,
                );
                return (
                  <>
                    <div className="row" style={{ alignItems: "flex-end", gap: 12 }}>
                      {totals.map((month) => {
                        const isCurrent = month.month === currentMonth?.month;
                        const heightPct = Math.max(
                          4,
                          (Math.abs(Number(month.total)) / max) * 100,
                        );
                        return (
                          <div
                            key={month.month}
                            style={{ flex: 1, display: "flex", flexDirection: "column" }}
                          >
                            <div
                              style={{
                                height: 100,
                                display: "flex",
                                alignItems: "flex-end",
                                border: "1px solid var(--line)",
                              }}
                              title={formatMoney(month.total, currency)}
                            >
                              <div
                                style={{
                                  width: "100%",
                                  height: `${heightPct}%`,
                                  background: isCurrent ? "var(--accent)" : "var(--muted)",
                                }}
                              />
                            </div>
                            <span className="muted" style={{ fontSize: "0.85em", marginTop: 4 }}>
                              {new Date(`${month.month}T00:00:00`).toLocaleDateString("en-SG", {
                                month: "short",
                              })}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                    {currentMonth && (
                      <p className="muted" style={{ marginTop: 8 }}>
                        {formatMoney(currentMonth.total, currency)} in {formatMonth(currentMonth.month)}
                        {currentMonth.month === highest.month
                          ? " — highest month in the six imported."
                          : `, vs a high of ${formatMoney(highest.total, currency)} in ${formatMonth(highest.month)}.`}
                      </p>
                    )}
                  </>
                );
              })()}
            </>
          )}
        </div>

        <div className="panel">
          <h2>Merchants</h2>
          {detail.companies.length === 0 && <p className="muted">No merchants yet.</p>}
          <table className="table">
            <thead>
              <tr>
                <th>Merchant</th>
                <th>Total</th>
                <th>Share</th>
                <th>Transactions</th>
              </tr>
            </thead>
            <tbody>
              {detail.companies.map((company) => (
                <tr key={company.merchant_id}>
                  <td>{company.name}</td>
                  <td className="amount">{formatMoney(company.total, currency)}</td>
                  <td>{Math.round(company.share * 100)}%</td>
                  <td>{company.transaction_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="panel">
          <h2>Cards used</h2>
          <ul className="list">
            {detail.cards_used.map((card) => (
              <li key={card.card_id} className="list-item row">
                <span>{card.nickname}</span>
                <span className="amount">{formatMoney(card.total, currency)}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="panel">
          <h2>Transactions</h2>
          {transactionsQuery.isLoading && <p className="muted">Loading transactions…</p>}
          {transactionsQuery.error && (
            <p className="error" role="alert">
              {transactionsQuery.error instanceof ApiError
                ? transactionsQuery.error.message
                : "Could not load transactions."}
            </p>
          )}
          {transactionsQuery.data && transactionsQuery.data.length === 0 && (
            <p className="muted">No transactions in this category.</p>
          )}
          {transactionsQuery.data && transactionsQuery.data.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th>Amount</th>
                </tr>
              </thead>
              <tbody>
                {transactionsQuery.data.map((transaction) => (
                  <tr key={transaction.id}>
                    <td>{formatDate(transaction.posted_on)}</td>
                    <td>
                      <Link to={`/dashboard/transactions/${transaction.id}`}>
                        {transaction.description}
                      </Link>
                    </td>
                    <td className="amount">
                      {formatMoney(transaction.amount, transaction.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}
