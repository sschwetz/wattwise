import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface Me {
  id: number;
  email: string;
  display_name: string | null;
  has_amber_api_key: boolean;
  has_avatar: boolean;
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<Me>("/api/v1/users/me"),
  });
}

export function useUpdateAmberApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (amberApiKey: string) =>
      apiFetch<Me>("/api/v1/users/me", {
        method: "PATCH",
        body: JSON.stringify({ amber_api_key: amberApiKey }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useUpdateDisplayName() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (displayName: string) =>
      apiFetch<Me>("/api/v1/users/me/profile", {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useUploadAvatar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return apiFetch<Me>("/api/v1/users/me/avatar", { method: "POST", body: formData });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
      queryClient.invalidateQueries({ queryKey: ["me", "avatar"] });
    },
  });
}

export function useRemoveAvatar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<Me>("/api/v1/users/me/avatar", { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
      queryClient.invalidateQueries({ queryKey: ["me", "avatar"] });
    },
  });
}

interface ChangePasswordPayload {
  current_password: string;
  new_password: string;
  totp_code: string;
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (payload: ChangePasswordPayload) =>
      apiFetch<void>("/api/v1/users/me/password", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}

interface RequestEmailChangePayload {
  new_email: string;
  current_password: string;
  totp_code: string;
}

interface RequestEmailChangeResponse {
  message: string;
}

export function useRequestEmailChange() {
  return useMutation({
    mutationFn: (payload: RequestEmailChangePayload) =>
      apiFetch<RequestEmailChangeResponse>("/api/v1/users/me/email/change/request", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}

export function useConfirmEmailChange() {
  return useMutation({
    mutationFn: (token: string) =>
      apiFetch<void>("/api/v1/users/me/email/change/confirm", {
        method: "POST",
        body: JSON.stringify({ token }),
      }),
  });
}

interface StartTotpReenrollPayload {
  current_password: string;
  totp_code: string;
}

interface StartTotpReenrollResponse {
  secret: string;
  otpauth_uri: string;
}

interface ConfirmTotpReenrollResponse {
  recovery_codes: string[];
}

export function useStartTotpReenroll() {
  return useMutation({
    mutationFn: (payload: StartTotpReenrollPayload) =>
      apiFetch<StartTotpReenrollResponse>("/api/v1/users/me/totp/reenroll/start", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}

export function useConfirmTotpReenroll() {
  return useMutation({
    mutationFn: (code: string) =>
      apiFetch<ConfirmTotpReenrollResponse>("/api/v1/users/me/totp/reenroll/confirm", {
        method: "POST",
        body: JSON.stringify({ code }),
      }),
  });
}
