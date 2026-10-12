import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { TotpSetupPage } from "./TotpSetupPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderWithState(state: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/totp-setup", state }]}>
      <AuthProvider>
        <Routes>
          <Route path="/totp-setup" element={<TotpSetupPage />} />
          <Route path="/" element={<div>Dashboard</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("TotpSetupPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
  });

  it("shows a re-login message when there is no setup token in router state", () => {
    renderWithState(null);
    expect(screen.getByRole("alert")).toHaveTextContent("log in again");
  });

  it("fetches the secret on mount and shows a QR code", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      secret: "JBSWY3DPEHPK3PXP",
      otpauth_uri: "otpauth://totp/WattWise:a@example.com?secret=JBSWY3DPEHPK3PXP&issuer=WattWise",
    });
    renderWithState({ setupToken: "a-setup-token" });
    expect(await screen.findByAltText("TOTP QR code")).toBeInTheDocument();
    expect(screen.getByText(/JBSWY3DPEHPK3PXP/)).toBeInTheDocument();
  });

  it("shows an error message when the initial setup/start fetch fails", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("network error"));
    renderWithState({ setupToken: "a-setup-token" });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load your authenticator setup. Please log in again.",
    );
    expect(screen.queryByAltText("TOTP QR code")).not.toBeInTheDocument();
  });

  it("shows recovery codes after a correct confirm code, then continues to / once acknowledged", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/x" })
      .mockResolvedValueOnce({
        access_token: "final-access-token",
        token_type: "bearer",
        recovery_codes: Array.from({ length: 10 }, (_, i) => `code${i}`),
      });
    renderWithState({ setupToken: "a-setup-token" });
    await screen.findByAltText("TOTP QR code");
    await userEvent.type(screen.getByPlaceholderText("6-digit code"), "123456");
    await userEvent.click(screen.getByText("Verify"));
    expect(await screen.findByText("code0")).toBeInTheDocument();

    const checkbox = screen.getByRole("checkbox");
    await userEvent.click(checkbox);
    await userEvent.click(screen.getByText("Continue"));
    expect(await screen.findByText("Dashboard")).toBeInTheDocument();
    expect(localStorage.getItem("access_token")).toBe("final-access-token");
  });
});
