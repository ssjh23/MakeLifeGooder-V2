/**
 * Statement status polling.
 *
 * Exists only because ADR-005 made processing asynchronous. Isolated in one
 * file so that swapping to server-sent events later touches this module and
 * nothing else, which is the upgrade path named in that decision.
 *
 * The important property is that polling terminates. `ready` and `failed` are
 * terminal, and a client that kept asking forever would turn one stuck
 * statement into steady background load from every open tab (TC-IMP-012).
 */

import { useQuery } from "@tanstack/react-query";
import { api, toApiError } from "../api/client";
import type { StatementResponse } from "../api/types";

// "needs_review" is also a stable rest state for this poll: extraction has
// finished and nothing further changes it except the user's own row edits and
// commit, each of which already drives its own refetch. Leaving it out here
// would poll every two seconds for as long as someone sits on the
// reconciliation screen.
const TERMINAL: ReadonlySet<string> = new Set(["ready", "failed", "needs_review"]);
const POLL_INTERVAL_MS = 2000;

export function useStatementPolling(statementId: string | null) {
  return useQuery({
    queryKey: ["statement", statementId],
    enabled: statementId !== null,
    queryFn: async (): Promise<StatementResponse> => {
      const { data, response } = await api.GET("/api/v1/statements/{statement_id}", {
        params: { path: { statement_id: statementId as string } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && TERMINAL.has(status) ? false : POLL_INTERVAL_MS;
    },
    // A statement that has reached a terminal state will not change again, so
    // there is nothing to gain from refetching it when the tab regains focus.
    refetchOnWindowFocus: false,
  });
}

export function isTerminal(status: string | undefined): boolean {
  return status !== undefined && TERMINAL.has(status);
}
