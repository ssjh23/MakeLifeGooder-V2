/**
 * Route guard for everything behind sign-in.
 *
 * Waits out `isLoading` rather than redirecting immediately: `useSession`
 * starts with no data, and redirecting on that first tick would bounce a
 * signed-in user to `/sign-in` for one frame on every hard refresh.
 */

import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useSession } from "../auth/useSession";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { isSignedIn, isLoading } = useSession();
  const location = useLocation();

  if (isLoading) return null;
  if (!isSignedIn) {
    return <Navigate to="/sign-in" replace state={{ from: location }} />;
  }
  return <>{children}</>;
}
