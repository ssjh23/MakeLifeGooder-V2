/**
 * Screens 08, 08b and 08c, plus category management.
 *
 * One `GET /account` fetch backs both the screen-08 summary and the
 * deletion screen's counts, so what a person is told they have matches what
 * they're told they're deleting (`AccountInventory`'s own docstring).
 */

import { NavLink, Route, Routes } from "react-router-dom";
import { CategoriesPanel } from "./CategoriesPanel";
import { DeleteAccountPanel } from "./DeleteAccountPanel";
import { ExportsPanel } from "./ExportsPanel";
import { useAccountInventory } from "./queries";

const TABS = [
  { to: "/account", label: "Overview", end: true },
  { to: "/account/categories", label: "Categories" },
  { to: "/account/exports", label: "Export data" },
  { to: "/account/delete", label: "Delete account" },
];

export function AccountFeature() {
  return (
    <section className="screen">
      <h1>Account</h1>
      <nav className="tabs">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) => `tab${isActive ? " is-active" : ""}`}
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <Routes>
        <Route index element={<Overview />} />
        <Route path="categories" element={<CategoriesPanel />} />
        <Route path="exports" element={<ExportsPanel />} />
        <Route path="delete" element={<DeleteAccountPanel />} />
      </Routes>
    </section>
  );
}

function Overview() {
  const inventory = useAccountInventory();

  if (inventory.isLoading) return <p className="muted">Loading…</p>;
  if (!inventory.data) return <p className="error">Could not load your account summary.</p>;

  return (
    <dl className="facts">
      <dt>Statements</dt>
      <dd>{inventory.data.statements}</dd>
      <dt>Transactions</dt>
      <dd>{inventory.data.transactions}</dd>
      <dt>Rules</dt>
      <dd>{inventory.data.rules}</dd>
      <dt>Categories</dt>
      <dd>{inventory.data.categories}</dd>
      <dt>Cards</dt>
      <dd>{inventory.data.cards}</dd>
    </dl>
  );
}
