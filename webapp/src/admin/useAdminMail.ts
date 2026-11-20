import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface ProviderField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  /** Always null for a secret field. */
  value: string | null;
  is_set: boolean;
}

export interface MailProvider {
  provider: string;
  label: string;
  available: boolean;
  is_active: boolean;
  fields: ProviderField[];
}

export interface OutboxSummary {
  pending: number;
  sent_24h: number;
  failed_24h: number;
  last_error: string | null;
}

const PROVIDERS_KEY = ["admin", "mail", "providers"] as const;
const SUMMARY_KEY = ["admin", "mail", "summary"] as const;
const SAVE_KEY = ["admin", "mail", "save"] as const;
const BASE = "/api/v1/admin/mail";

export function useMailProviders() {
  return useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: () => apiFetch<MailProvider[]>(`${BASE}/providers`),
  });
}

export function useOutboxSummary() {
  return useQuery({
    queryKey: SUMMARY_KEY,
    queryFn: () => apiFetch<OutboxSummary>(`${BASE}/outbox/summary`),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}

function useProviderMutation<V>(request: (variables: V) => Promise<MailProvider>, mutationKey?: readonly string[]) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey,
    mutationFn: request,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PROVIDERS_KEY }),
  });
}

export function useSaveProviderSettings(provider: string) {
  return useProviderMutation(
    (settings: Record<string, string>) =>
      apiFetch<MailProvider>(`${BASE}/providers/${provider}`, {
        method: "PUT",
        body: JSON.stringify({ settings }),
      }),
    SAVE_KEY,
  );
}

/** True while any settings save is in flight; the controls that act on the provider wait for it. */
export function useIsSavingSettings(): boolean {
  return useIsMutating({ mutationKey: SAVE_KEY }) > 0;
}

export function useActivateProvider(provider: string) {
  return useProviderMutation(() => apiFetch<MailProvider>(`${BASE}/providers/${provider}/activate`, { method: "POST" }));
}

export function useDeactivateProvider(provider: string) {
  return useProviderMutation(() => apiFetch<MailProvider>(`${BASE}/providers/${provider}/deactivate`, { method: "POST" }));
}

export function useSendTestEmail() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<{ queued: boolean }>(`${BASE}/test`, { method: "POST" }),
    onSuccess: () => {
      // The summary and the providers list both change once a test is queued.
      void queryClient.invalidateQueries({ queryKey: PROVIDERS_KEY });
      void queryClient.invalidateQueries({ queryKey: SUMMARY_KEY });
    },
  });
}
