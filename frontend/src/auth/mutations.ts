/**
 * Auth mutations. Screens 00 and 01, plus the password-reset pair.
 *
 * Every one of these ends by invalidating the `session` query rather than
 * writing the response into it by hand, so `useSession` — and therefore
 * `NavGuard` — never disagrees with what the server just did.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, toApiError } from "../api/client";

export function useRegister() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { email: string; password: string; name?: string }) => {
      const { data, response } = await api.POST("/api/v1/auth/register", {
        body: { email: input.email, password: input.password, name: input.name ?? null },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["session"] }),
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { email: string; password: string }) => {
      const { data, response } = await api.POST("/api/v1/auth/login", {
        body: { email: input.email, password: input.password },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["session"] }),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { response } = await api.POST("/api/v1/auth/logout", {});
      if (!response.ok) throw await toApiError(response);
    },
    // The session is gone the moment the cookie is cleared, whether or not the
    // request round-trips cleanly, so this clears the cache eagerly rather
    // than waiting for a refetch that would otherwise 401.
    onSuccess: () => queryClient.setQueryData(["session"], null),
  });
}

export function useRequestPasswordReset() {
  return useMutation({
    mutationFn: async (email: string) => {
      const { response } = await api.POST("/api/v1/auth/password-reset/request", {
        body: { email },
      });
      if (!response.ok) throw await toApiError(response);
    },
  });
}

export function useConfirmPasswordReset() {
  return useMutation({
    mutationFn: async (input: { token: string; newPassword: string }) => {
      const { response } = await api.POST("/api/v1/auth/password-reset/confirm", {
        body: { token: input.token, new_password: input.newPassword },
      });
      if (!response.ok) throw await toApiError(response);
    },
  });
}
