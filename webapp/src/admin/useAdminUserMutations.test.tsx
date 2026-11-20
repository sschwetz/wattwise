import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { useGrantSiteAccess, useRemoveSiteAccess, useUpdateAdminUser } from "./useAdminUserMutations";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const ME = 5;

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  const meLoaded = () => waitFor(() => expect(queryClient.getQueryData(["me"])).toBeDefined());
  return { invalidate, wrapper, meLoaded };
}

function invalidatedKeys(invalidate: ReturnType<typeof setup>["invalidate"]) {
  return invalidate.mock.calls.map(([filters]) => filters?.queryKey);
}

describe("admin user mutations", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockImplementation(async (path: string) => (path === "/api/v1/users/me" ? { id: ME } : {}) as never);
  });

  it("refreshes the signed-in admin's site list when they change their own grant", async () => {
    const { invalidate, wrapper, meLoaded } = setup();
    const { result } = renderHook(() => useGrantSiteAccess(ME), { wrapper });
    await meLoaded();
    await result.current.mutateAsync({ siteId: 7, role: "manager" });
    expect(invalidatedKeys(invalidate)).toEqual(expect.arrayContaining([["admin", "users"], ["me"], ["sites"]]));
  });

  it("refreshes the site list when the admin removes their own grant", async () => {
    const { invalidate, wrapper, meLoaded } = setup();
    const { result } = renderHook(() => useRemoveSiteAccess(ME), { wrapper });
    await meLoaded();
    await result.current.mutateAsync(7);
    expect(invalidatedKeys(invalidate)).toContainEqual(["sites"]);
  });

  it("leaves the site list alone for another user's grant or a profile change", async () => {
    const { invalidate, wrapper, meLoaded } = setup();
    const grant = renderHook(() => useGrantSiteAccess(ME + 1), { wrapper });
    const profile = renderHook(() => useUpdateAdminUser(ME), { wrapper });
    await meLoaded();
    await grant.result.current.mutateAsync({ siteId: 7, role: "viewer" });
    await profile.result.current.mutateAsync({ display_name: "X" });
    expect(invalidatedKeys(invalidate)).not.toContainEqual(["sites"]);
  });
});
