/**
 * Rules data access. Screens 07 to 07e.
 *
 * Preview and execution are always separate calls here, never the same
 * mutation with a flag — a rule preview and a reapply-across-history preview
 * both exist so a person sees the effect before committing to it, and a
 * rerun can silently overwrite decisions someone made by hand.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, toApiError } from "../../api/client";
import type {
  ReapplyPreviewRequest,
  ReapplyRequest,
  ResolveConflictRequest,
  RuleCreate,
  RulePreviewRequest,
  RuleUpdate,
} from "../../api/types";

export function useRules() {
  return useQuery({
    queryKey: ["rules"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/rules", {});
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

export function usePreviewRule() {
  return useMutation({
    mutationFn: async (body: RulePreviewRequest) => {
      const { data, response } = await api.POST("/api/v1/rules/preview", { body });
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useCreateRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: RuleCreate) => {
      const { data, response } = await api.POST("/api/v1/rules", { body });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rules"] }),
  });
}

export function useUpdateRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { ruleId: string; body: RuleUpdate }) => {
      const { data, response } = await api.PATCH("/api/v1/rules/{rule_id}", {
        params: { path: { rule_id: input.ruleId } },
        body: input.body,
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rules"] }),
  });
}

export function useDeleteRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (ruleId: string) => {
      const { response } = await api.DELETE("/api/v1/rules/{rule_id}", {
        params: { path: { rule_id: ruleId } },
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rules"] }),
  });
}

export function useRuleConflicts() {
  return useQuery({
    queryKey: ["rules", "conflicts"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/rules/conflicts", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useResolveConflict() {
  const queryClient = useQueryClient();
  return useMutation({
    // The path param is named `conflict_id`, but `RuleConflictSummary` only
    // ever gives us `rule_id` — confirmed against `RuleService.resolve_conflict`
    // (backend/app/services/rules.py), whose first parameter is literally
    // `rule_id`, so that's exactly what belongs here.
    mutationFn: async (input: { conflictId: string; body: ResolveConflictRequest }) => {
      const { response } = await api.POST("/api/v1/rules/conflicts/{conflict_id}/resolve", {
        params: { path: { conflict_id: input.conflictId } },
        body: input.body,
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rules", "conflicts"] });
      queryClient.invalidateQueries({ queryKey: ["rules"] });
    },
  });
}

export function useReapplyPreview() {
  return useMutation({
    mutationFn: async (body: ReapplyPreviewRequest) => {
      const { data, response } = await api.POST("/api/v1/rules/reapply/preview", { body });
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useReapply() {
  return useMutation({
    mutationFn: async (body: ReapplyRequest) => {
      const { response } = await api.POST("/api/v1/rules/reapply", { body });
      if (!response.ok) throw await toApiError(response);
    },
  });
}
