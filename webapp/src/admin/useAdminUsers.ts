import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface AdminUserSite {
  site_id: number;
  site_name: string;
  role: "owner" | "manager" | "viewer";
}

export interface AdminUser {
  id: number;
  email: string;
  display_name: string | null;
  is_admin: boolean;
  has_avatar: boolean;
  totp_enabled: boolean;
  email_confirmed: boolean;
  has_amber_api_key: boolean;
  sites: AdminUserSite[];
}

export function useAdminUsers() {
  return useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => apiFetch<AdminUser[]>("/api/v1/admin/users"),
  });
}

/** False for NaN, zero and negative ids, which never name a user. */
export function isUserId(id: number): boolean {
  return Number.isInteger(id) && id > 0;
}

export function useAdminUser(id: number) {
  return useQuery({
    queryKey: ["admin", "users", id],
    enabled: isUserId(id),
    queryFn: () => apiFetch<AdminUser>(`/api/v1/admin/users/${id}`),
  });
}
