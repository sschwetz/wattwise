import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ConfirmEmailChangePage } from "./ConfirmEmailChangePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage(token = "a-valid-token") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/confirm-email-change?token=${token}`]}>
        <ConfirmEmailChangePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ConfirmEmailChangePage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("confirms the email change and shows a success message", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(undefined);
    renderPage("a-valid-token");

    await userEvent.click(screen.getByText("Confirm email change"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/email/change/confirm",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ token: "a-valid-token" }) }),
    );
    expect(await screen.findByText(/email address has been updated/i)).toBeInTheDocument();
  });

  it("shows an error when the token is invalid or expired", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValueOnce(
      new ApiError(400, "invalid_token", "This confirmation link is invalid or has expired", null),
    );
    renderPage("a-bad-token");

    await userEvent.click(screen.getByText("Confirm email change"));

    expect(await screen.findByRole("alert")).toHaveTextContent("This confirmation link is invalid or has expired");
  });
});
