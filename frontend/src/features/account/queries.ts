/**
 * Account and category data access. Screens 08 to 08c, plus category
 * management (no dedicated screen number — see the module docstring on
 * `app/schemas/account.py`: categories are created inline during review but
 * the wireframe draws no management surface for them, so this is the gap
 * being closed).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, toApiError } from "../../api/client";
import type {
  CategoryCreate,
  CategoryMergeRequest,
  CategoryUpdate,
  DeleteAccountRequest,
  ExportRequest,
} from "../../api/types";

export function useAccountInventory() {
  return useQuery({
    queryKey: ["account", "inventory"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/account", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useCategories() {
  return useQuery({
    queryKey: ["categories"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/categories", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useCards() {
  return useQuery({
    queryKey: ["cards"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/cards", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useCreateCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CategoryCreate) => {
      const { data, response } = await api.POST("/api/v1/categories", { body });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["categories"] }),
  });
}

export function useUpdateCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { categoryId: string; body: CategoryUpdate }) => {
      const { data, response } = await api.PATCH("/api/v1/categories/{category_id}", {
        params: { path: { category_id: input.categoryId } },
        body: input.body,
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["categories"] }),
  });
}

export function useMergeCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { categoryId: string; body: CategoryMergeRequest }) => {
      const { data, response } = await api.POST("/api/v1/categories/{category_id}/merge", {
        params: { path: { category_id: input.categoryId } },
        body: input.body,
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["categories"] }),
  });
}

export function useDeleteCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (categoryId: string) => {
      const { response } = await api.DELETE("/api/v1/categories/{category_id}", {
        params: { path: { category_id: categoryId } },
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["categories"] }),
  });
}

export function useStartExport() {
  return useMutation({
    mutationFn: async (body: ExportRequest) => {
      const { data, response } = await api.POST("/api/v1/exports", { body });
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useExportStatus(exportId: string | null) {
  return useQuery({
    queryKey: ["exports", exportId],
    enabled: exportId !== null,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/exports/{export_id}", {
        params: { path: { export_id: exportId as string } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    refetchInterval: (query) => (query.state.data?.status === "pending" ? 2000 : false),
  });
}

export function useDeleteAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: DeleteAccountRequest) => {
      const { response } = await api.DELETE("/api/v1/account", { body });
      if (!response.ok) throw await toApiError(response);
    },
    // The session is gone the moment the server accepts this, same reasoning
    // as `useLogout` in `auth/mutations.ts`.
    onSuccess: () => queryClient.setQueryData(["session"], null),
  });
}
