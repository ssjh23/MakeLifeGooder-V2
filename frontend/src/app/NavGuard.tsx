/**
 * Navigation, with three tabs inert until a statement has been committed.
 *
 * Inert, not hidden. Screen 02's copy tells the user those sections unlock
 * after the first statement, and copy that refers to something invisible is
 * confusing rather than reassuring. Showing where the product goes is also how
 * a new user learns what it does.
 *
 * The gate is `hasStatements` from the server, which flips on the first
 * successful commit rather than the first upload.
 */

import { NavLink, useNavigate } from "react-router-dom";
import { useLogout } from "../auth/mutations";
import { useSession } from "../auth/useSession";

interface Tab {
  to: string;
  label: string;
  /** Whether this tab needs a committed statement to be useful. */
  needsData: boolean;
}

const TABS: Tab[] = [
  { to: "/import", label: "Import", needsData: false },
  { to: "/cards", label: "Cards", needsData: false },
  { to: "/review", label: "Review", needsData: true },
  { to: "/dashboard", label: "Dashboard", needsData: true },
  { to: "/rules", label: "Rules", needsData: true },
];

export function NavGuard() {
  const { hasStatements, isSignedIn } = useSession();
  const logout = useLogout();
  const navigate = useNavigate();
  if (!isSignedIn) return null;

  async function handleSignOut() {
    await logout.mutateAsync();
    navigate("/sign-in", { replace: true });
  }

  return (
    <nav className="nav" aria-label="Main">
      <span className="brand">Ledger</span>
      {TABS.map((tab) => {
        const locked = tab.needsData && !hasStatements;
        return locked ? (
          <span
            key={tab.to}
            className="nav-item nav-item--locked"
            aria-disabled="true"
            title="Unlocks after your first statement is imported"
          >
            {tab.label}
          </span>
        ) : (
          <NavLink
            key={tab.to}
            to={tab.to}
            className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}
          >
            {tab.label}
          </NavLink>
        );
      })}
      <NavLink to="/account" className="nav-item nav-item--right">
        Account
      </NavLink>
      <button type="button" className="button--link" onClick={() => void handleSignOut()}>
        Sign out
      </button>
    </nav>
  );
}
