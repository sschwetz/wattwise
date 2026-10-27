import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiFetchBlob } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { Header } from "./Header";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn(), apiFetchBlob: vi.fn() };
});

function renderHeader(path = "/sites/7") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/sites/:siteId/*" element={<Header />} />
            <Route path="/sites/new" element={<Header />} />
            <Route path="/account" element={<Header />} />
            <Route path="/help" element={<Header />} />
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
    vi.mocked(apiFetchBlob).mockReset();
    localStorage.clear();
    localStorage.setItem("access_token", "fake-token");
  });

  it("shows the display name when one is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_avatar: false, has_amber_api_key: false,
    });
    renderHeader();
    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
  });

  it("truncates a long label instead of letting it overflow off-screen", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "A Very Long Display Name That Should Not Push The Avatar Off Screen",
      has_avatar: false, has_amber_api_key: false,
    });
    renderHeader();
    const label = await screen.findByText("A Very Long Display Name That Should Not Push The Avatar Off Screen");
    expect(label.className).toContain("truncate");
    expect(screen.getByRole("button", { name: /a very long display name/i }).className).toContain("min-w-0");
  });

  it("falls back to the email when no display name is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: null,
      has_avatar: false, has_amber_api_key: false,
    });
    renderHeader();
    expect(await screen.findByText("ada@example.com")).toBeInTheDocument();
  });

  it("opens a dropdown with Account settings and Log off", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_avatar: false, has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    expect(screen.getByRole("menuitem", { name: "Account settings" })).toHaveAttribute("href", "/account");
    expect(screen.getByRole("menuitem", { name: "Site settings" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("menuitem", { name: "Log off" })).toBeInTheDocument();
  });

  it("closes the dropdown on outside click", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_avatar: false, has_amber_api_key: false,
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
      has_avatar: false, has_amber_api_key: false,
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
      has_avatar: false, has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Log off" }));

    expect(localStorage.getItem("access_token")).toBeNull();
  });

  it("shows the uploaded avatar image when one is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_avatar: true,
      has_amber_api_key: false,
    });
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();

    renderHeader();
    await screen.findByText("Ada Lovelace");
    expect(await screen.findByAltText("")).toHaveAttribute("src", "blob:mock-url");
  });

  describe("Help menu item", () => {
    const sites = [
      { id: 4, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true },
      { id: 9, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart", enabled: true },
    ];

    function mockApi(siteList: typeof sites) {
      vi.mocked(apiFetch).mockImplementation((path: string) =>
        Promise.resolve(
          path === "/api/v1/sites"
            ? siteList
            : { id: 1, email: "ada@example.com", display_name: "Ada Lovelace", has_avatar: false, has_amber_api_key: false },
        ),
      );
    }

    async function openMenu(path: string, siteList = sites) {
      mockApi(siteList);
      renderHeader(path);
      await userEvent.click(await screen.findByRole("button", { name: /ada lovelace/i }));
    }

    it("sits between Account settings and Log off", async () => {
      await openMenu("/sites/7");
      const labels = screen.getAllByRole("menuitem").map((item) => item.textContent);
      expect(labels).toEqual(["Account settings", "Site settings", "Help", "Log off"]);
    });

    it("targets the current site on a site route", async () => {
      await openMenu("/sites/7/billing");
      expect(screen.getByRole("menuitem", { name: "Help" })).toHaveAttribute("href", "/sites/7/help");
    });

    it("targets the first site on /account", async () => {
      await openMenu("/account");
      await waitFor(() =>
        expect(screen.getByRole("menuitem", { name: "Help" })).toHaveAttribute("href", "/sites/4/help"),
      );
    });

    it("does not mistake /sites/new for a site id", async () => {
      await openMenu("/sites/new");
      await waitFor(() =>
        expect(screen.getByRole("menuitem", { name: "Help" })).toHaveAttribute("href", "/sites/4/help"),
      );
    });

    it("falls back to /help when the user has no sites", async () => {
      await openMenu("/account", []);
      await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/api/v1/sites"));
      expect(screen.getByRole("menuitem", { name: "Help" })).toHaveAttribute("href", "/help");
    });

    it("closes the menu when clicked", async () => {
      await openMenu("/sites/7");
      await userEvent.click(screen.getByRole("menuitem", { name: "Help" }));
      expect(screen.queryByRole("menuitem", { name: "Log off" })).not.toBeInTheDocument();
    });
  });
});
