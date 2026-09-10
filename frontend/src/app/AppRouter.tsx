/**
 * Routes for all twenty-seven screens.
 *
 * Only Import is built. The rest resolve to a placeholder naming the screen and
 * its endpoints, so the shape of the product is visible from the running app
 * and adding a screen means replacing one element rather than wiring routing.
 */

import { Navigate, Route, Routes } from "react-router-dom";
import { NavGuard } from "./NavGuard";
import { Placeholder, SCREENS } from "../features/Placeholder";
import { ImportScreen } from "../features/import/ImportScreen";
import { RequestIdBoundary } from "../shared/RequestIdBoundary";

export function AppRouter() {
  return (
    <div className="app">
      <NavGuard />
      <main>
        <RequestIdBoundary>
          <Routes>
            <Route path="/" element={<Navigate to="/import" replace />} />

            {/* Built */}
            <Route path="/import" element={<ImportScreen />} />

            {/* Routed, not built */}
            <Route path="/sign-in" element={<Placeholder {...SCREENS.signIn} />} />
            <Route path="/first-import" element={<Placeholder {...SCREENS.firstImport} />} />
            <Route path="/cards" element={<Placeholder {...SCREENS.cards} />} />
            <Route path="/review" element={<Placeholder {...SCREENS.review} />} />
            <Route path="/dashboard" element={<Placeholder {...SCREENS.dashboard} />} />
            <Route path="/rules" element={<Placeholder {...SCREENS.rules} />} />
            <Route path="/account" element={<Placeholder {...SCREENS.account} />} />

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
