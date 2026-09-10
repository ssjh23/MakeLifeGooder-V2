import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AppRouter } from "./app/AppRouter";
import "./styles.css";

/**
 * No Redux, and no global store of any kind.
 *
 * Almost everything on screen is server state, held in the query cache. A
 * second copy in a client store would be a second source of truth to keep
 * correct, and the failure mode is a dashboard that disagrees with the
 * database. UI state stays in the component that owns it.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // An authentication failure is an answer, not a transient fault, so
      // retrying it just delays the redirect.
      retry: (failureCount, error) => {
        const status = (error as { status?: number }).status;
        if (status === 401 || status === 404 || status === 501) return false;
        return failureCount < 2;
      },
      staleTime: 10_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRouter />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
