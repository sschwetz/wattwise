import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { LoginPage } from "./LoginPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    apiFetch: vi.fn(),
    ApiError: actual.ApiError,
  };
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="state">{JSON.stringify(location.state)}</div>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/totp-setup" element={<LocationProbe />} />
        <Route path="/totp-verify" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function submitLogin() {
  await userEvent.type(screen.getByPlaceholderText("Email"), "a@example.com");
  await userEvent.type(screen.getByPlaceholderText("Password"), "a-password");
  await userEvent.click(screen.getByRole("button", { name: /log in/i }));
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("navigates to /totp-setup with the setup_token when enrollment is required", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      status: "totp_setup_required",
      setup_token: "a-setup-token",
      mfa_token: null,
    });
    renderPage();
    await submitLogin();
    expect(await screen.findByTestId("state")).toHaveTextContent("a-setup-token");
  });

  it("navigates to /totp-verify with the mfa_token when already enrolled", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      status: "totp_required",
      setup_token: null,
      mfa_token: "an-mfa-token",
    });
    renderPage();
    await submitLogin();
    expect(await screen.findByTestId("state")).toHaveTextContent("an-mfa-token");
  });

  it("shows an error on invalid credentials and does not navigate", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(401, "invalid_credentials", "Email or password is incorrect", null));
    renderPage();
    await submitLogin();
    expect(await screen.findByRole("alert")).toHaveTextContent("incorrect");
  });
});
