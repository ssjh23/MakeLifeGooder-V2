/**
 * Cards data access. One file so the query key is spelled the same way
 * everywhere and a mutation never forgets to invalidate it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, toApiError } from "../../api/client";
import type {
  ArchiveRequest,
  ArchivePreview,
  CardCreate,
  CardResponse,
  CardUpdate,
} from "../../api/types";

export const cardsKey = ["cards"] as const;

export function useCards() {
  return useQuery({
    queryKey: cardsKey,
    queryFn: async (): Promise<CardResponse[]> => {
      const { data, response } = await api.GET("/api/v1/cards", {});
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useCard(cardId: string | undefined) {
  const { data, ...rest } = useCards();
  return { ...rest, data: data?.find((card) => card.id === cardId) };
}

export function useCreateCard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: CardCreate): Promise<CardResponse> => {
      const { data, response } = await api.POST("/api/v1/cards", { body: input });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: cardsKey }),
  });
}

export function useUpdateCard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { cardId: string; update: CardUpdate }): Promise<CardResponse> => {
      const { data, response } = await api.PATCH("/api/v1/cards/{card_id}", {
        params: { path: { card_id: input.cardId } },
        body: input.update,
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: cardsKey }),
  });
}

export function useArchivePreview(cardId: string | undefined) {
  return useQuery({
    queryKey: ["cards", cardId, "archive-preview"],
    enabled: cardId !== undefined,
    queryFn: async (): Promise<ArchivePreview> => {
      const { data, response } = await api.GET("/api/v1/cards/{card_id}/archive-preview", {
        params: { path: { card_id: cardId as string } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
  });
}

export function useArchiveCard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      cardId: string;
      request: ArchiveRequest;
    }): Promise<CardResponse> => {
      const { data, response } = await api.POST("/api/v1/cards/{card_id}/archive", {
        params: { path: { card_id: input.cardId } },
        body: input.request,
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: cardsKey }),
  });
}

export function useRestoreCard() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (cardId: string): Promise<CardResponse> => {
      const { data, response } = await api.POST("/api/v1/cards/{card_id}/restore", {
        params: { path: { card_id: cardId } },
      });
      if (!data) throw await toApiError(response);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: cardsKey }),
  });
}

export function useDeleteCardStatements() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (cardId: string): Promise<void> => {
      const { response } = await api.DELETE("/api/v1/cards/{card_id}/statements", {
        params: { path: { card_id: cardId } },
        body: { confirm: true },
      });
      if (!response.ok) throw await toApiError(response);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: cardsKey }),
  });
}
