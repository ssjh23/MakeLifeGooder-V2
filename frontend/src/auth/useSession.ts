/**
 * The session.
 *
 * `hasStatements` is read from the server rather than inferred by fetching
 * statements and checking for an empty array, because those two answers differ
 * during an import: a statement can exist, be uploaded, and still not have been
 * committed. It flips on the first successful **commit**, not the first upload.
 *
 * That distinction is what screen 02 depends on. An upload that fails to
 * reconcile leaves the ledger empty, and unlocking a dashboard that would show
 * nothing is worse than leaving it locked.
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export interface Session {
  id: string;
  email: string;
  name: string | null;
  hasStatements: boolean;
}

export function useSession() {
  const query = useQuery({
    queryKey: ["session"],
    queryFn: async (): Promise<Session | null> => {
      const { data, response } = await api.GET("/api/v1/me", {});
      // 401 is not an error here, it is the answer: nobody is signed in.
      if (response.status === 401) return null;
      if (!data) return null;
      return {
        id: data.id,
        email: data.email,
        name: data.name ?? null,
        hasStatements: data.has_statements,
      };
    },
    retry: false,
    staleTime: 30_000,
  });

  return {
    session: query.data ?? null,
    isLoading: query.isLoading,
    isSignedIn: query.data != null,
    hasStatements: query.data?.hasStatements ?? false,
  };
}
