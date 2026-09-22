/**
 * Routes for all twenty-seven screens.
 *
 * Only Import is built. The rest resolve to a placeholder naming the screen and
 * its endpoints, so the shape of the product is visible from the running app
 * and adding a screen means replacing one element rather than wiring routing.
 */

import { Route, Routes } from "react-router-dom";
import { NavGuard } from "./NavGuard";
import { RequireAuth } from "./RequireAuth";
import { RootRedirect } from "./RootRedirect";
import { Placeholder } from "../features/Placeholder";
import { SignIn } from "../features/auth/SignIn";
import { SignUp } from "../features/auth/SignUp";
import { ForgotPassword } from "../features/auth/ForgotPassword";
import { FirstImport } from "../features/onboarding/FirstImport";
import { ImportFeature } from "../features/import/ImportFeature";
import { CardsFeature } from "../features/cards/CardsFeature";
import { ReviewFeature } from "../features/review/ReviewFeature";
import { DashboardFeature } from "../features/dashboard/DashboardFeature";
import { RulesFeature } from "../features/rules/RulesFeature";
import { AccountFeature } from "../features/account/AccountFeature";
import { RequestIdBoundary } from "../shared/RequestIdBoundary";

export function AppRouter() {
  return (
    <div className="app">
      <NavGuard />
      <main>
        <RequestIdBoundary>
          <Routes>
            <Route path="/" element={<RootRedirect />} />

            {/* Screens 00, 01 */}
            <Route path="/sign-in" element={<SignIn />} />
            <Route path="/sign-up" element={<SignUp />} />
            <Route path="/forgot-password" element={<ForgotPassword />} />

            {/* Everything else needs a session. */}
            <Route
              path="/first-import"
              element={
                <RequireAuth>
                  <FirstImport />
                </RequireAuth>
              }
            />
            <Route
              path="/import/*"
              element={
                <RequireAuth>
                  <ImportFeature />
                </RequireAuth>
              }
            />
            <Route
              path="/cards/*"
              element={
                <RequireAuth>
                  <CardsFeature />
                </RequireAuth>
              }
            />
            <Route
              path="/review/*"
              element={
                <RequireAuth>
                  <ReviewFeature />
                </RequireAuth>
              }
            />
            <Route
              path="/dashboard/*"
              element={
                <RequireAuth>
                  <DashboardFeature />
                </RequireAuth>
              }
            />
            <Route
              path="/rules/*"
              element={
                <RequireAuth>
                  <RulesFeature />
                </RequireAuth>
              }
            />
            <Route
              path="/account/*"
              element={
                <RequireAuth>
                  <AccountFeature />
                </RequireAuth>
              }
            />

            <Route
              path="*"
              element={
                <Placeholder
                  screen="404"
                  title="No such screen"
                  endpoints={[]}
                  note="Every route in the product is listed in AppRouter."
                />
              }
            />
          </Routes>
        </RequestIdBoundary>
      </main>
    </div>
  );
}
