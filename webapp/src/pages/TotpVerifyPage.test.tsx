import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { TotpVerifyPage } from "./TotpVerifyPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderWithState(state: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/totp-verify", state }]}>
      <AuthProvider>
        <Routes>
          <Route path="/totp-verify" element={<TotpVerifyPage />} />
          <Route path="/" element={<div>Dashboard</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("TotpVerifyPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
  });

  it("shows a re-login message when there is no mfa token in router state", () => {
    renderWithState(null);
    expect(screen.getByRole("alert")).toHaveTextContent("log in again");
  });

  it("logs in and navigates to / on a correct code", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ access_token: "final-token", token_type: "bearer" });
    renderWithState({ mfaToken: "an-mfa-token" });
    await userEvent.type(screen.getByPlaceholderText("6-digit code"), "123456");
    await userEvent.click(screen.getByText("Verify"));
    expect(await screen.findByText("Dashboard")).toBeInTheDocument();
    expect(localStorage.getItem("access_token")).toBe("final-token");
  });

  it("toggles to a recovery-code placeholder", async () => {
    renderWithState({ mfaToken: "an-mfa-token" });
    await userEvent.click(screen.getByText("Use a recovery code instead"));
    expect(screen.getByPlaceholderText("Recovery code")).toBeInTheDocument();
  });

  it("shows an error on an incorrect code", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(401, "invalid_code", "Incorrect code", null));
    renderWithState({ mfaToken: "an-mfa-token" });
    await userEvent.type(screen.getByPlaceholderText("6-digit code"), "000000");
    await userEvent.click(screen.getByText("Verify"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect code");
  });
});
