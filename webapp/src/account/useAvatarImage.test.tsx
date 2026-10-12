import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetchBlob } from "../api/client";
import { useAvatarImage } from "./useAvatarImage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetchBlob: vi.fn() };
});

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useAvatarImage", () => {
  beforeEach(() => {
    vi.mocked(apiFetchBlob).mockReset();
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
  });

  it("returns null and does not fetch when there is no avatar", () => {
    const { result } = renderHook(() => useAvatarImage(false), { wrapper });
    expect(result.current).toBeNull();
    expect(apiFetchBlob).not.toHaveBeenCalled();
  });

  it("fetches and returns an object URL when an avatar exists", async () => {
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    const { result } = renderHook(() => useAvatarImage(true), { wrapper });
    await waitFor(() => expect(result.current).toBe("blob:mock-url"));
    expect(apiFetchBlob).toHaveBeenCalledWith("/api/v1/users/me/avatar");
  });

  it("revokes the object URL when the avatar is removed", async () => {
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    const { result, rerender } = renderHook(({ hasAvatar }) => useAvatarImage(hasAvatar), {
      wrapper,
      initialProps: { hasAvatar: true },
    });
    await waitFor(() => expect(result.current).toBe("blob:mock-url"));

    rerender({ hasAvatar: false });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    expect(result.current).toBeNull();
  });

  it("revokes the object URL on unmount", async () => {
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    const { result, unmount } = renderHook(() => useAvatarImage(true), { wrapper });
    await waitFor(() => expect(result.current).toBe("blob:mock-url"));

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });
});
