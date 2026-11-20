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

export function useAdminUser(id: number) {
  return useQuery({
    queryKey: ["admin", "users", id],
    queryFn: () => apiFetch<AdminUser>(`/api/v1/admin/users/${id}`),
  });
}
