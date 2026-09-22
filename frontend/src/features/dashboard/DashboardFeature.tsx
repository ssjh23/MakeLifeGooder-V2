/**
 * Dashboard. Screens 06, 06b and 06c.
 *
 * Coarse to fine: how much (index), then who (category detail), then why
 * (transaction detail). Three screens, three routes, mounted at `/dashboard/*`
 * by `AppRouter`.
 */

import { Route, Routes } from "react-router-dom";
import { DashboardOverview } from "./DashboardOverview";
import { CategoryDetailScreen } from "./CategoryDetailScreen";
import { TransactionDetailScreen } from "./TransactionDetailScreen";

export function DashboardFeature() {
  return (
    <Routes>
      <Route index element={<DashboardOverview />} />
      <Route path="categories/:categoryId" element={<CategoryDetailScreen />} />
      <Route path="transactions/:transactionId" element={<TransactionDetailScreen />} />
    </Routes>
  );
}
