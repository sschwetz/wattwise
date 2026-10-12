import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { RegisterPage } from "./RegisterPage";

vi.mock("../api/client", () => ({ apiFetch: vi.fn() }));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/register"]}>
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RegisterPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("registers with just an email and shows a check-your-email message instead of navigating to /login", async () => {
    // The account (if new) isn't usable until the emailed confirmation link
    // is clicked and a password chosen there, so this must NOT navigate
    // straight to /login.
    vi.mocked(apiFetch).mockResolvedValue({ message: "Check your email to confirm your account before logging in." });
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("Email"), "a@example.com");
    await userEvent.click(screen.getByText("Create account"));
    expect(await screen.findByText("Check your email")).toBeInTheDocument();
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/auth/register",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ email: "a@example.com" }) }),
    );
  });

  it("does not render a password field", () => {
    // Choosing a password happens on the confirmation page, not here --
    // see ConfirmRegistrationPage. A password field on this page would let
    // an attacker's re-registration of someone else's unconfirmed email
    // bake in a password the real owner would unknowingly adopt by
    // clicking their confirmation link.
    renderPage();
    expect(screen.queryByPlaceholderText("Password")).not.toBeInTheDocument();
  });
});
