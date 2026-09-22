/**
 * Where "/" sends you. Not a screen of its own.
 *
 * Signed out goes to sign-in. Signed in with no committed statement goes to
 * the empty state (02), because the dashboard, review and rules tabs are
 * inert until `hasStatements` flips (see `NavGuard`) and landing on one of
 * them would show nothing. Signed in with data goes straight to the
 * dashboard, which is the screen the product is for.
 */

import { Navigate } from "react-router-dom";
import { useSession } from "../auth/useSession";

export function RootRedirect() {
  const { isSignedIn, isLoading, hasStatements } = useSession();

  if (isLoading) return null;
  if (!isSignedIn) return <Navigate to="/sign-in" replace />;
  if (!hasStatements) return <Navigate to="/first-import" replace />;
  return <Navigate to="/dashboard" replace />;
}
