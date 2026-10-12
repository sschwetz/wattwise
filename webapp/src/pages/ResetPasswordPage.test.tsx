import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ResetPasswordPage } from "./ResetPasswordPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/reset-password${search}`]}>
      <Routes>
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ResetPasswordPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("submits the token from the URL and the new password, then navigates to /login", async () => {
    vi.mocked(apiFetch).mockResolvedValue(undefined);
    renderPage("?token=abc123");
    await userEvent.type(screen.getByPlaceholderText("New password"), "a-new-password");
    await userEvent.click(screen.getByText("Reset password"));
    expect(await screen.findByText("Login page")).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/auth/password-reset/confirm",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ token: "abc123", new_password: "a-new-password" }),
      }),
    );
  });

  it("shows an error for an invalid or expired token", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(400, "invalid_token", "This reset link is invalid or has expired", null));
    renderPage("?token=expired");
    await userEvent.type(screen.getByPlaceholderText("New password"), "a-new-password");
    await userEvent.click(screen.getByText("Reset password"));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
  });
});
