import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AdminSettingsPage } from "./AdminSettingsPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/admin/:tab?" element={<AdminSettingsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AdminSettingsPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValue([]);
  });

  it("opens the Email tab at /admin", async () => {
    renderAt("/admin");
    expect(screen.getByRole("heading", { name: "Admin Settings" })).toBeInTheDocument();
    expect(await screen.findByText(/No email provider has been declared yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Email" })).toHaveAttribute("aria-current", "page");
  });

  it("shows the Users tab at /admin/users", async () => {
    renderAt("/admin/users");
    expect(await screen.findByRole("searchbox", { name: "Search users" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("aria-current", "page");
  });

  it("falls back to the Email tab for an unknown tab", async () => {
    renderAt("/admin/nope");
    expect(await screen.findByText(/No email provider has been declared yet/)).toBeInTheDocument();
  });
});
