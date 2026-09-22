/**
 * Rules. Screens 07 to 07e.
 *
 * Every confirmed category becomes a rule. Preview and execution stay on
 * separate endpoints throughout this feature — see `queries.ts`.
 */

import { NavLink, Route, Routes } from "react-router-dom";
import { RuleConflicts } from "./RuleConflicts";
import { RuleList } from "./RuleList";
import { RuleReapply } from "./RuleReapply";

const TABS = [
  { to: "/rules", label: "Rules", end: true },
  { to: "/rules/conflicts", label: "Conflicts" },
  { to: "/rules/reapply", label: "Reapply to history" },
];

export function RulesFeature() {
  return (
    <section className="screen">
      <h1>Rules</h1>
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
        <Route index element={<RuleList />} />
        <Route path="conflicts" element={<RuleConflicts />} />
        <Route path="reapply" element={<RuleReapply />} />
      </Routes>
    </section>
  );
}
