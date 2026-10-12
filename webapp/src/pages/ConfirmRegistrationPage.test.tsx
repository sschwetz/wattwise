import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ConfirmRegistrationPage } from "./ConfirmRegistrationPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage(token = "a-valid-token") {
  return render(
    <MemoryRouter initialEntries={[`/confirm-registration?token=${token}`]}>
      <Routes>
        <Route path="/confirm-registration" element={<ConfirmRegistrationPage />} />
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ConfirmRegistrationPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("confirms the account with a chosen password and navigates to /login", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(undefined);
    renderPage("a-valid-token");

    await userEvent.type(screen.getByPlaceholderText("Password"), "a-password");
    await userEvent.type(screen.getByPlaceholderText("Confirm password"), "a-password");
    await userEvent.click(screen.getByText("Confirm account"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/auth/register/confirm",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ token: "a-valid-token", password: "a-password" }),
      }),
    );
    expect(await screen.findByText(/account is ready/i)).toBeInTheDocument();

    await userEvent.click(screen.getByText("Go to login"));
    expect(await screen.findByText("Login page")).toBeInTheDocument();
  });

  it("shows an error and does not navigate when passwords don't match", async () => {
    renderPage("a-valid-token");
    await userEvent.type(screen.getByPlaceholderText("Password"), "a-password");
    await userEvent.type(screen.getByPlaceholderText("Confirm password"), "different");
    await userEvent.click(screen.getByText("Confirm account"));
    expect(await screen.findByRole("alert")).toHaveTextContent("don't match");
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("shows an error when the token is invalid or expired", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValueOnce(
      new ApiError(400, "invalid_token", "This confirmation link is invalid or has expired", null),
    );
    renderPage("a-bad-token");

    await userEvent.type(screen.getByPlaceholderText("Password"), "a-password");
    await userEvent.type(screen.getByPlaceholderText("Confirm password"), "a-password");
    await userEvent.click(screen.getByText("Confirm account"));

    expect(await screen.findByRole("alert")).toHaveTextContent("This confirmation link is invalid or has expired");
  });
});
