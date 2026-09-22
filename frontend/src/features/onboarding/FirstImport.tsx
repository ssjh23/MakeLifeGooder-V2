/**
 * Screen 02: the empty state.
 *
 * Review, Dashboard and Rules render inert in `NavGuard` rather than being
 * hidden, so this screen's copy explains what they're waiting for instead of
 * pretending the product only has one tab.
 */

import { Link } from "react-router-dom";
import { useSession } from "../../auth/useSession";

export function FirstImport() {
  const { session } = useSession();

  return (
    <section className="screen">
      <h1>{session?.name ? `Welcome, ${session.name}` : "Welcome to Ledger"}</h1>
      <p className="muted">
        Nothing is imported yet. Upload a statement PDF to get started — the rows
        get checked against the total printed on the document before anything
        joins your ledger.
      </p>

      <div className="button-row">
        <Link to="/import" className="button button--primary">
          Import your first statement
        </Link>
        <Link to="/cards" className="button">
          Set up a card first
        </Link>
      </div>

      <h2>Once you've imported</h2>
      <p className="muted">
        Review, Dashboard and Rules unlock the moment your first statement is
        committed — not just uploaded. An upload that fails to reconcile leaves
        the ledger empty on purpose, so those tabs stay locked until there is
        something in them worth showing.
      </p>
    </section>
  );
}
