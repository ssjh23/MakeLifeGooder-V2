/**
 * Screen 06: the dashboard.
 *
 * Five views, coarse to fine, matching the wireframe's own numbering:
 *   01 months stacked     -- is this month unusual, and which categories moved
 *   02 categories + companies -- where the money in each category went
 *   03 by card             -- the same period, split by which card paid
 *   04 every category, ranked -- the full ledger, totalling back to the statement
 *   05 individual transactions -- every line, filterable
 *
 * `banner.locked` is not decoration. While anything is unclassified the
 * server is telling us its own totals are unreliable, so the month and
 * category numbers render muted and unlinked-from-trust rather than as if
 * nothing were wrong (the invariant: dashboard totals stay locked while
 * anything is unclassified).
 */

import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, toApiError } from "../../api/client";
import type {
  CardResponse,
  CategoryResponse,
  DashboardResponse,
  TransactionResponse,
} from "../../api/types";
import { formatDate, formatMoney, formatMonth } from "../../shared/money";

type Range = "month" | "6m" | "year";

const RANGES: { value: Range; label: string }[] = [
  { value: "month", label: "This month" },
  { value: "6m", label: "6 months" },
  { value: "year", label: "Year" },
];

//: A small, mostly-grayscale palette so one category (the largest mover)
//: can still be picked out with the accent colour, consistent with the
//: rest of the app's one-accent design rather than a full colour wheel.
const SEGMENT_COLOURS = ["var(--accent)", "var(--ink)", "#666666", "#999999", "#c4c4c4"];
const OTHER_COLOUR = "var(--line-strong)";

const TX_PAGE_SIZE = 25;

type SortOption = "date_desc" | "date_asc" | "amount_desc" | "amount_asc";

export function DashboardOverview() {
  const [searchParams] = useSearchParams();
  const [range, setRange] = useState<Range>("month");
  // Seeded from `?search=` -- the "See other rows" link on screen 06c's
  // company field lands here with the merchant name pre-filled, since the
  // transactions band has no dedicated merchant filter of its own.
  const [txSearch, setTxSearch] = useState(searchParams.get("search") ?? "");
  const [txCategoryId, setTxCategoryId] = useState("");
  const [txCardId, setTxCardId] = useState("");
  const [txSort, setTxSort] = useState<SortOption>("date_desc");
  const [txVisibleCount, setTxVisibleCount] = useState(TX_PAGE_SIZE);

  const query = useQuery({
    queryKey: ["dashboard", range],
    queryFn: async (): Promise<DashboardResponse> => {
      const { data, response } = await api.GET("/api/v1/dashboard", {
        params: { query: { range } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  const transactionsQuery = useQuery({
    queryKey: ["transactions", { txCategoryId, txCardId, txSearch, txSort }],
    queryFn: async (): Promise<TransactionResponse[]> => {
      const { data, response } = await api.GET("/api/v1/transactions", {
        params: {
          query: {
            category_id: txCategoryId || undefined,
            card_id: txCardId || undefined,
            search: txSearch || undefined,
            sort: txSort,
          },
        },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });

  // The dashboard's own `categories` band (below) is scoped to the selected
  // range and only lists categories with a nonzero total in it -- a
  // transaction whose category has no recent spend (an older statement, or
  // a category picked up by a rule between refreshes) would look up nothing
  // there and show as "Unclassified" despite genuinely having a category.
  // The individual-transactions band isn't range-scoped at all, so it needs
  // the tenant's full category list, not the range-limited one.
  const allCategoriesQuery = useQuery({
    queryKey: ["categories"],
    queryFn: async (): Promise<CategoryResponse[]> => {
      const { data, response } = await api.GET("/api/v1/categories", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });
  const allCategories = allCategoriesQuery.data ?? [];

  // Same reasoning as allCategoriesQuery: the dashboard's own `cards` band
  // (below) is scoped to the selected range too, so a transaction whose
  // card has no total there -- an older statement, most of the time --
  // would look up nothing and show a blank Card column despite genuinely
  // having one.
  const allCardsQuery = useQuery({
    queryKey: ["cards"],
    queryFn: async (): Promise<CardResponse[]> => {
      const { data, response } = await api.GET("/api/v1/cards", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });
  const allCards = allCardsQuery.data ?? [];

  // Hooks below must run unconditionally on every render (Rules of Hooks),
  // so the loading/error early returns come after them, not before -- an
  // empty `months` array is a perfectly safe input while `query.data` is
  // still undefined.
  const months = query.data?.months ?? [];

  const topCategoryNames = useMemo(() => {
    const totals = new Map<string, number>();
    for (const month of months) {
      for (const [name, amount] of Object.entries(month.by_category)) {
        totals.set(name, (totals.get(name) ?? 0) + Math.abs(Number(amount)));
      }
    }
    return [...totals.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, SEGMENT_COLOURS.length)
      .map(([name]) => name);
  }, [months]);

  const currentMonth = months.at(-1);
  const previousMonth = months.at(-2);

  const mover = useMemo(() => {
    if (!currentMonth || !previousMonth) return null;
    let bestName: string | null = null;
    let bestDelta = 0;
    for (const name of topCategoryNames) {
      const now = Math.abs(Number(currentMonth.by_category[name] ?? "0"));
      const before = Math.abs(Number(previousMonth.by_category[name] ?? "0"));
      const delta = now - before;
      if (Math.abs(delta) > Math.abs(bestDelta)) {
        bestDelta = delta;
        bestName = name;
      }
    }
    if (!bestName || bestDelta === 0) return null;
    const before = Math.abs(Number(previousMonth.by_category[bestName] ?? "0"));
    const pct = before > 0 ? Math.round((bestDelta / before) * 100) : null;
    return { name: bestName, delta: bestDelta, pct };
  }, [currentMonth, previousMonth, topCategoryNames]);

  if (query.isLoading) return <section className="screen">Loading…</section>;

  if (query.error) {
    return (
      <section className="screen">
        <h1>Dashboard</h1>
        <p className="error" role="alert">
          {query.error instanceof ApiError ? query.error.message : "Could not load the dashboard."}
        </p>
      </section>
    );
  }

  const dashboard = query.data;
  if (!dashboard) return null;

  const { banner, categories, cards } = dashboard;
  const currency = "SGD";
  const locked = banner.locked;
  const maxMonthTotal = Math.max(1, ...months.map((m) => Math.abs(Number(m.total))));

  // -- Filter option lists for the transactions band (05) -------------------

  const transactions = transactionsQuery.data ?? [];
  const visibleTransactions = transactions.slice(0, txVisibleCount);
  const filteredTotal = transactions.reduce((sum, t) => sum + Number(t.amount), 0);

  return (
    <section className="screen">
      <h1>Dashboard</h1>

      {banner.unclassified !== "0.00" && (
        <div className={`banner${locked ? " banner--locked" : ""}`} role="status">
          <strong>{locked ? "Totals are locked." : "Some money is still unclassified."}</strong>
          <p className="muted" style={{ margin: "6px 0 0" }}>
            {formatMoney(banner.classified, currency)} classified,{" "}
            {formatMoney(banner.unclassified, currency)} still unassigned.{" "}
            {locked && "Finish review to unlock accurate totals."}
          </p>
        </div>
      )}

      <div className="row">
        <div className="tabs">
          {RANGES.map((r) => (
            <button
              key={r.value}
              type="button"
              className={`tab${range === r.value ? " is-active" : ""}`}
              onClick={() => setRange(r.value)}
            >
              {r.label}
            </button>
          ))}
        </div>
        <Link to="/account/exports" className="button">
          Export CSV
        </Link>
      </div>

      {/* -- 01: months stacked ------------------------------------------ */}
      <div className="panel">
        <h2>01 · Months stacked</h2>
        <p className="muted">Is this month unusual, and which categories moved.</p>

        {months.length === 0 && <p className="muted">No committed statements in this range.</p>}

        {months.length > 0 && (
          <>
            <div className="row" style={{ alignItems: "flex-end", gap: 12 }}>
              {months.map((month) => {
                const total = Math.abs(Number(month.total));
                const heightPct = Math.max(4, (total / maxMonthTotal) * 100);
                const isCurrent = month.month === currentMonth?.month;
                return (
                  <div
                    key={month.month}
                    style={{
                      flex: 1,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "stretch",
                    }}
                  >
                    <div
                      style={{
                        height: 120,
                        display: "flex",
                        flexDirection: "column-reverse",
                        border: isCurrent ? "2px solid var(--accent)" : "1px solid var(--line)",
                      }}
                      title={formatMoney(month.total, currency)}
                    >
                      <div
                        style={{
                          height: `${heightPct}%`,
                          display: "flex",
                          flexDirection: "column-reverse",
                          width: "100%",
                        }}
                      >
                        {topCategoryNames.map((name, i) => {
                          const amount = Math.abs(Number(month.by_category[name] ?? "0"));
                          const share = total > 0 ? (amount / total) * 100 : 0;
                          return share > 0 ? (
                            <div
                              key={name}
                              style={{ height: `${share}%`, background: SEGMENT_COLOURS[i] }}
                            />
                          ) : null;
                        })}
                        {(() => {
                          const namedTotal = topCategoryNames.reduce(
                            (sum, name) => sum + Math.abs(Number(month.by_category[name] ?? "0")),
                            0,
                          );
                          const otherShare = total > 0 ? ((total - namedTotal) / total) * 100 : 0;
                          return otherShare > 0 ? (
                            <div style={{ height: `${otherShare}%`, background: OTHER_COLOUR }} />
                          ) : null;
                        })()}
                      </div>
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

            <div className="row" style={{ marginTop: 8, flexWrap: "wrap", gap: 10 }}>
              {topCategoryNames.map((name, i) => (
                <span key={name} className="muted" style={{ fontSize: "0.85em" }}>
                  <span
                    aria-hidden
                    style={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      marginRight: 4,
                      background: SEGMENT_COLOURS[i],
                    }}
                  />
                  {name}
                </span>
              ))}
              <span className="muted" style={{ fontSize: "0.85em" }}>
                <span
                  aria-hidden
                  style={{
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    marginRight: 4,
                    background: OTHER_COLOUR,
                  }}
                />
                Everything else
              </span>
            </div>

            {mover && (
              <p className="muted" style={{ marginTop: 8 }}>
                {mover.name} drove the {currentMonth ? formatMonth(currentMonth.month) : ""} change
                {mover.pct !== null && `: ${mover.pct > 0 ? "+" : ""}${mover.pct}%`} on{" "}
                {formatMoney(String(Math.abs(mover.delta) / 100), currency)}.
              </p>
            )}
          </>
        )}
      </div>

      {/* -- 02: categories and the companies inside them ------------------ */}
      <div className="panel">
        <h2>02 · Categories, and the companies inside them</h2>
        <p className="muted">Where the money in each category actually went.</p>

        {categories.length === 0 && <p className="muted">Nothing classified yet.</p>}

        {categories.length > 0 && (
          <div className="grid">
            {categories.map((category) => (
              <div key={category.category_id} className="card-tile">
                <div className="row">
                  <Link to={`categories/${category.category_id}`}>
                    <strong>{category.name}</strong>
                  </Link>
                </div>
                <div className={`amount${locked ? " muted" : ""}`} style={{ fontSize: "1.3em" }}>
                  {formatMoney(category.total, currency)}
                </div>
                {category.change != null && (
                  <span
                    className={category.change > 0 ? "change-positive" : "change-negative"}
                    style={{ fontSize: "0.85em" }}
                  >
                    {category.change > 0 ? "+" : ""}
                    {Math.round(category.change * 100)}% vs last month
                  </span>
                )}

                <ul className="list" style={{ marginTop: 8 }}>
                  {category.top_merchants.map((merchant) => (
                    <li key={merchant.merchant_id} className="row" style={{ fontSize: "0.9em" }}>
                      <span>{merchant.name}</span>
                      <span className="amount">{formatMoney(merchant.total, currency)}</span>
                    </li>
                  ))}
                  {category.other_merchants_count > 0 && (
                    <li className="row muted" style={{ fontSize: "0.9em" }}>
                      <span>{category.other_merchants_count} others</span>
                      <span className="amount">
                        {category.other_merchants_total
                          ? formatMoney(category.other_merchants_total, currency)
                          : null}
                      </span>
                    </li>
                  )}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* -- 03: by card ---------------------------------------------------- */}
      <div className="panel">
        <h2>03 · By card</h2>
        <p className="muted">The same period, split by which card paid for it.</p>

        {cards.length === 0 && <p className="muted">No cards tagged in this range.</p>}

        {cards.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Card</th>
                <th>Total</th>
                <th>Rows</th>
                <th>Largest category</th>
                <th>Split</th>
              </tr>
            </thead>
            <tbody>
              {cards.map((card) => {
                const cardTotal = Math.abs(Number(card.total)) || 1;
                return (
                  <tr key={card.card_id}>
                    <td>
                      {card.colour && (
                        <span
                          aria-hidden
                          style={{
                            display: "inline-block",
                            width: 10,
                            height: 10,
                            borderRadius: 5,
                            background: card.colour,
                            marginRight: 8,
                          }}
                        />
                      )}
                      {card.nickname}
                    </td>
                    <td className={`amount${locked ? " muted" : ""}`}>
                      {formatMoney(card.total, currency)}
                    </td>
                    <td>{card.rows}</td>
                    <td>
                      {card.largest_category
                        ? `${card.largest_category.name} · ${formatMoney(card.largest_category.total, currency)}`
                        : "—"}
                    </td>
                    <td style={{ minWidth: 120 }}>
                      <div className="bar-track" style={{ display: "flex" }}>
                        {Object.entries(card.by_category).map(([name, amount], i) => {
                          const share = (Math.abs(Number(amount)) / cardTotal) * 100;
                          return (
                            <div
                              key={name}
                              title={`${name}: ${formatMoney(amount, currency)}`}
                              style={{
                                width: `${share}%`,
                                background: SEGMENT_COLOURS[i % SEGMENT_COLOURS.length],
                                height: "100%",
                              }}
                            />
                          );
                        })}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* -- 04: every category, ranked -------------------------------------- */}
      <div className="panel">
        <h2>04 · Every category, ranked</h2>
        <p className="muted">The full ledger, totalling back to the statement.</p>

        {categories.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Category</th>
                <th>Amount</th>
                <th>Share</th>
                <th>Rows</th>
                <th>Largest merchant</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((category) => (
                <tr key={category.category_id}>
                  <td>
                    <Link to={`categories/${category.category_id}`}>{category.name}</Link>
                  </td>
                  <td className={`amount${locked ? " muted" : ""}`}>
                    {formatMoney(category.total, currency)}
                  </td>
                  <td>{Math.round(category.share * 100)}%</td>
                  <td>{category.rows}</td>
                  <td>
                    {category.top_merchants[0]
                      ? `${category.top_merchants[0].name} · ${formatMoney(category.top_merchants[0].total, currency)}`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td>
                  <strong>Total</strong>
                </td>
                <td className="amount">
                  <strong>
                    {formatMoney(
                      String(categories.reduce((sum, c) => sum + Number(c.total), 0)),
                      currency,
                    )}
                  </strong>
                </td>
                <td colSpan={3} />
              </tr>
            </tfoot>
          </table>
        )}
      </div>

      {/* -- 05: individual transactions -------------------------------------- */}
      <div className="panel">
        <h2>05 · Individual transactions</h2>
        <p className="muted">Every line as it appeared on the statement — filter by category or company.</p>

        <div className="field-row">
          <div className="field">
            <label htmlFor="tx-search">Search description</label>
            <input
              id="tx-search"
              value={txSearch}
              onChange={(event) => {
                setTxSearch(event.target.value);
                setTxVisibleCount(TX_PAGE_SIZE);
              }}
              placeholder="e.g. grab, fairprice…"
            />
          </div>
          <div className="field">
            <label htmlFor="tx-category">Category</label>
            <select
              id="tx-category"
              value={txCategoryId}
              onChange={(event) => {
                setTxCategoryId(event.target.value);
                setTxVisibleCount(TX_PAGE_SIZE);
              }}
            >
              <option value="">All categories</option>
              {allCategories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="tx-card">Card</label>
            <select
              id="tx-card"
              value={txCardId}
              onChange={(event) => {
                setTxCardId(event.target.value);
                setTxVisibleCount(TX_PAGE_SIZE);
              }}
            >
              <option value="">All cards</option>
              {allCards.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.nickname}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="tx-sort">Sort</label>
            <select
              id="tx-sort"
              value={txSort}
              onChange={(event) => setTxSort(event.target.value as SortOption)}
            >
              <option value="date_desc">Newest first</option>
              <option value="date_asc">Oldest first</option>
              <option value="amount_desc">Amount, high to low</option>
              <option value="amount_asc">Amount, low to high</option>
            </select>
          </div>
        </div>

        {transactionsQuery.isLoading && <p className="muted">Loading transactions…</p>}
        {transactionsQuery.error && (
          <p className="error" role="alert">
            {transactionsQuery.error instanceof ApiError
              ? transactionsQuery.error.message
              : "Could not load transactions."}
          </p>
        )}
        {transactions.length === 0 && !transactionsQuery.isLoading && (
          <p className="muted">No transactions match these filters.</p>
        )}

        {transactions.length > 0 && (
          <>
            <p className="muted">
              {Math.min(txVisibleCount, transactions.length)} of {transactions.length} rows shown
            </p>
            <table className="table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description as printed</th>
                  <th>Company</th>
                  <th>Category</th>
                  <th>Card</th>
                  <th>Amount</th>
                </tr>
              </thead>
              <tbody>
                {visibleTransactions.map((transaction) => {
                  const category = allCategories.find((c) => c.id === transaction.category_id);
                  const card = allCards.find((c) => c.id === transaction.card_id);
                  return (
                    <tr key={transaction.id}>
                      <td>{formatDate(transaction.posted_on)}</td>
                      <td>
                        <Link to={`transactions/${transaction.id}`}>{transaction.description}</Link>
                      </td>
                      <td>
                        {transaction.merchant_name ??
                          transaction.provenance?.rule_pattern ??
                          transaction.descriptor_key ??
                          "—"}
                      </td>
                      <td>
                        {category ? (
                          <span className="badge badge--muted">{category.name}</span>
                        ) : (
                          <span className="badge">Unclassified</span>
                        )}{" "}
                        {transaction.provenance?.rule_id && (
                          <span
                            className="badge badge--accent"
                            title={
                              transaction.provenance.rule_pattern
                                ? `Matched rule: ${transaction.provenance.rule_pattern}`
                                : "Matched a standing rule"
                            }
                          >
                            rule
                          </span>
                        )}
                      </td>
                      <td>{card?.nickname ?? "—"}</td>
                      <td className="amount">{formatMoney(transaction.amount, transaction.currency)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {txVisibleCount < transactions.length && (
              <button
                type="button"
                className="button--link"
                onClick={() => setTxVisibleCount(transactions.length)}
              >
                Show all {transactions.length} transactions
              </button>
            )}

            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted">
                {(txCategoryId && allCategories.find((c) => c.id === txCategoryId)?.name) ||
                  "All transactions"}{" "}
                · {transactions.length} row{transactions.length === 1 ? "" : "s"}
              </span>
              <div className="row" style={{ gap: 12 }}>
                <strong className="amount">{formatMoney(String(filteredTotal), currency)}</strong>
                <Link to="/account/exports" className="button">
                  Export this view
                </Link>
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
