import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export type InviteStatus = "invited" | "accepted" | "declined" | "revoked";

export interface Invite {
  id: number;
  site_id: number;
  email: string;
  status: InviteStatus;
  created_at: string;
  accepted_at: string | null;
  declined_at: string | null;
  revoked_at: string | null;
  inviter_user_id: number;
  inviter_email: string;
}

export interface PendingInvite {
  id: number;
  site_id: number;
  site_name: string;
  inviter_email: string;
  created_at: string;
}

export function usePendingInvites() {
  return useQuery({
    queryKey: ["invites", "pending"],
    queryFn: () => apiFetch<PendingInvite[]>("/api/v1/invites/pending"),
  });
}

export function useSiteInvites(siteId: number) {
  return useQuery({
    queryKey: ["siteInvites", siteId],
    queryFn: () => apiFetch<Invite[]>(`/api/v1/sites/${siteId}/invites`),
  });
}

/** Mutations change who can see which site, so refresh every list that shows it. */
function useInvalidateAfterInviteChange(siteId?: number) {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["sites"] });
    queryClient.invalidateQueries({ queryKey: ["invites"] });
    queryClient.invalidateQueries({ queryKey: siteId === undefined ? ["siteInvites"] : ["siteInvites", siteId] });
  };
}

export function useCreateInvite(siteId: number) {
  const onSuccess = useInvalidateAfterInviteChange(siteId);
  return useMutation({
    mutationFn: (input: { email: string }) =>
      apiFetch<Invite>(`/api/v1/sites/${siteId}/invites`, { method: "POST", body: JSON.stringify(input) }),
    onSuccess,
  });
}

export function useRevokeInvite(siteId: number) {
  const onSuccess = useInvalidateAfterInviteChange(siteId);
  return useMutation({
    mutationFn: (inviteId: number) =>
      apiFetch<Invite>(`/api/v1/sites/${siteId}/invites/${inviteId}/revoke`, { method: "POST" }),
    onSuccess,
  });
}

export function useAcceptInvite() {
  const onSuccess = useInvalidateAfterInviteChange();
  return useMutation({
    mutationFn: (inviteId: number) => apiFetch<Invite>(`/api/v1/invites/${inviteId}/accept`, { method: "POST" }),
    onSuccess,
  });
}

export function useDeclineInvite() {
  const onSuccess = useInvalidateAfterInviteChange();
  return useMutation({
    mutationFn: (inviteId: number) => apiFetch<Invite>(`/api/v1/invites/${inviteId}/decline`, { method: "POST" }),
    onSuccess,
  });
}
