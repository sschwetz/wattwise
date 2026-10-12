import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ForgotPasswordPage } from "./ForgotPasswordPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ForgotPasswordPage />
    </MemoryRouter>,
  );
}

async function submitEmail(email = "a@example.com") {
  await userEvent.type(screen.getByPlaceholderText("Email"), email);
  await userEvent.click(screen.getByText("Send reset link"));
}

describe("ForgotPasswordPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows the generic confirmation message after submit, regardless of outcome", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ message: "If that email is registered, a password reset link has been sent." });
    renderPage();
    await submitEmail();
    expect(await screen.findByText(/password reset link has been sent/)).toBeInTheDocument();
  });

  it("shows the API error message when the request fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(422, "validation_error", "That email address is not valid", null));
    renderPage();
    await submitEmail("a@example.com");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That email address is not valid");
    // The form stays usable rather than silently hanging.
    expect(screen.getByText("Send reset link")).toBeInTheDocument();
  });

  it("shows a fallback message when the failure is not an Error", async () => {
    vi.mocked(apiFetch).mockRejectedValue("network down");
    renderPage();
    await submitEmail();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not send a reset link. Please try again.");
  });
});
