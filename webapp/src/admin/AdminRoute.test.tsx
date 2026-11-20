import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AdminRoute } from "./AdminRoute";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const me = (isAdmin: boolean) => ({
  id: 1,
  email: "ada@example.com",
  display_name: null,
  has_avatar: false,
  has_amber_api_key: false,
  is_admin: isAdmin,
});

function renderGuard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route path="/" element={<p>Home page</p>} />
          <Route element={<AdminRoute />}>
            <Route path="/admin" element={<p>Admin content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AdminRoute", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("renders its child route for an admin", async () => {
    vi.mocked(apiFetch).mockResolvedValue(me(true));
    renderGuard();
    expect(await screen.findByText("Admin content")).toBeInTheDocument();
  });

  it("sends a non-admin to the home page", async () => {
    vi.mocked(apiFetch).mockResolvedValue(me(false));
    renderGuard();
    expect(await screen.findByText("Home page")).toBeInTheDocument();
    expect(screen.queryByText("Admin content")).not.toBeInTheDocument();
  });

  it("sends the user home when me fails to load", async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error("boom"));
    renderGuard();
    expect(await screen.findByText("Home page")).toBeInTheDocument();
  });

  it("renders nothing while me is loading", async () => {
    vi.mocked(apiFetch).mockReturnValue(new Promise(() => {}));
    const { container } = renderGuard();
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
