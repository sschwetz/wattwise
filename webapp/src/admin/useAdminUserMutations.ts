import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMe } from "../account/useAccount";
import { apiFetch } from "../api/client";
import type { AdminUser } from "./useAdminUsers";

export interface AdminUserPatch {
  display_name?: string | null;
  email?: string;
  is_admin?: boolean;
}

export type GrantRole = "manager" | "viewer";

function send<T>(userId: number, path: string, method: string, body?: unknown): Promise<T> {
  return apiFetch<T>(`/api/v1/admin/users/${userId}${path}`, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

/** A mutation on one user. It refreshes the admin lists, and the signed-in
 * user's own profile when the target is that user. */
function useUserMutation<V>(userId: number, request: (variables: V) => Promise<unknown>) {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  return useMutation({
    mutationFn: request,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      if (me?.id === userId) void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useUpdateAdminUser(userId: number) {
  return useUserMutation(userId, (patch: AdminUserPatch) => send<AdminUser>(userId, "", "PATCH", patch));
}

export function useRemoveUserAvatar(userId: number) {
  return useUserMutation(userId, () => send<AdminUser>(userId, "/avatar", "DELETE"));
}

export function useSetUserAmberKey(userId: number) {
  return useUserMutation(userId, (amberApiKey: string) =>
    send<AdminUser>(userId, "/amber-key", "PUT", { amber_api_key: amberApiKey }),
  );
}

export function useClearUserAmberKey(userId: number) {
  return useUserMutation(userId, () => send<AdminUser>(userId, "/amber-key", "DELETE"));
}

export function useGrantSiteAccess(userId: number) {
  return useUserMutation(userId, ({ siteId, role }: { siteId: number; role: GrantRole }) =>
    send<AdminUser>(userId, `/sites/${siteId}`, "PUT", { role }),
  );
}

export function useRemoveSiteAccess(userId: number) {
  return useUserMutation(userId, (siteId: number) => send<AdminUser>(userId, `/sites/${siteId}`, "DELETE"));
}

export function useSendPasswordReset(userId: number) {
  return useUserMutation(userId, () => send<void>(userId, "/password-reset", "POST"));
}

export function useResetUserTotp(userId: number) {
  return useUserMutation(userId, () => send<AdminUser>(userId, "/totp/reset", "POST"));
}
