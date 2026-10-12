import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { Header } from "./Header";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderHeader() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/sites/7"]}>
          <Routes>
            <Route path="/sites/7" element={<Header />} />
            <Route path="/account" element={<p>Account page</p>} />
            <Route path="/login" element={<p>Login page</p>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("Header", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
    localStorage.setItem("access_token", "fake-token");
  });

  it("shows the display name when one is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
  });

  it("falls back to the email when no display name is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: null,
      has_amber_api_key: false,
    });
    renderHeader();
    expect(await screen.findByText("ada@example.com")).toBeInTheDocument();
  });

  it("opens a dropdown with Account settings and Log off", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    expect(screen.getByRole("menuitem", { name: "Account settings" })).toHaveAttribute("href", "/account");
    expect(screen.getByRole("menuitem", { name: "Log off" })).toBeInTheDocument();
  });

  it("closes the dropdown on outside click", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    expect(screen.getByRole("menuitem", { name: "Log off" })).toBeInTheDocument();

    await userEvent.click(document.body);
    expect(screen.queryByRole("menuitem", { name: "Log off" })).not.toBeInTheDocument();
  });

  it("closes the dropdown on Escape", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    expect(screen.getByRole("menuitem", { name: "Log off" })).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menuitem", { name: "Log off" })).not.toBeInTheDocument();
  });

  it("clears the stored access token when Log off is clicked", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Log off" }));

    expect(localStorage.getItem("access_token")).toBeNull();
  });
});
