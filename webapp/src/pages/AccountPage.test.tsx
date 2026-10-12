import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AccountPage } from "./AccountPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/account"]}>
        <Routes>
          <Route path="/account" element={<AccountPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AccountPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows no key set yet when none is configured", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false });
    renderPage();
    expect(await screen.findByText(/no amber api key set yet/i)).toBeInTheDocument();
  });

  it("shows the key is configured when one already exists", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: true });
    renderPage();
    expect(await screen.findByText(/amber api key is configured/i)).toBeInTheDocument();
  });

  it("masks the Amber API key input", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);
    expect(screen.getByLabelText("Amber API key")).toHaveAttribute("type", "password");
  });

  it("saves a new Amber API key", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: true })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: true });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Amber API key"), "psk_my_secret_key");
    await userEvent.click(screen.getByText("Save"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ amber_api_key: "psk_my_secret_key" }) }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("shows an error when saving fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(422, "invalid_key", "amber_api_key must not be empty", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Amber API key"), "x");
    await userEvent.click(screen.getByText("Save"));

    expect(await screen.findByRole("alert")).toHaveTextContent("amber_api_key must not be empty");
  });

  it("changes the password when current password and TOTP code are correct", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockResolvedValueOnce(undefined);
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password"), "old-password");
    await userEvent.type(screen.getByLabelText("New password"), "new-password");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "new-password");
    await userEvent.type(screen.getByLabelText("Authenticator code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/password",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ current_password: "old-password", new_password: "new-password", totp_code: "123456" }),
      }),
    );
    expect(await screen.findByText("Password changed.")).toBeInTheDocument();
  });

  it("rejects a mismatched password confirmation without calling the API", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password"), "old-password");
    await userEvent.type(screen.getByLabelText("New password"), "new-password");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "something-else");
    await userEvent.type(screen.getByLabelText("Authenticator code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/do not match/i);
    expect(apiFetch).toHaveBeenCalledTimes(1); // only the initial useMe call
  });

  it("shows the API error when the password change is rejected", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(401, "invalid_credentials", "Current password is incorrect", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password"), "wrong-password");
    await userEvent.type(screen.getByLabelText("New password"), "new-password");
    await userEvent.type(screen.getByLabelText("Confirm new password"), "new-password");
    await userEvent.type(screen.getByLabelText("Authenticator code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Current password is incorrect");
  });

  it("requests an email change when current password and TOTP code are correct", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockResolvedValueOnce({ message: "Check your new email address for a confirmation link." });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("New email"), "new@example.com");
    await userEvent.type(screen.getByLabelText("Current password for email change"), "old-password");
    await userEvent.type(screen.getByLabelText("Authenticator code for email change"), "123456");
    await userEvent.click(screen.getByText("Send confirmation email"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/email/change/request",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ new_email: "new@example.com", current_password: "old-password", totp_code: "123456" }),
      }),
    );
    expect(await screen.findByText("Check your new email address for a confirmation link.")).toBeInTheDocument();
  });

  it("shows the API error when the email change request is rejected", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(409, "email_taken", "An account with this email already exists", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("New email"), "taken@example.com");
    await userEvent.type(screen.getByLabelText("Current password for email change"), "old-password");
    await userEvent.type(screen.getByLabelText("Authenticator code for email change"), "123456");
    await userEvent.click(screen.getByText("Send confirmation email"));

    expect(await screen.findByRole("alert")).toHaveTextContent("An account with this email already exists");
  });

  it("walks through replacing the authenticator and shows new recovery codes", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockResolvedValueOnce({ secret: "NEWSECRET234567", otpauth_uri: "otpauth://totp/WattWise:me@example.com?secret=NEWSECRET234567&issuer=WattWise" })
      .mockResolvedValueOnce({ recovery_codes: Array.from({ length: 10 }, (_, i) => `code${i}`) });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password for two-factor reset"), "old-password");
    await userEvent.type(screen.getByLabelText("Current authenticator code"), "111111");
    await userEvent.click(screen.getByText("Replace authenticator"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/totp/reenroll/start",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ current_password: "old-password", totp_code: "111111" }),
      }),
    );
    await screen.findByText(/manual entry key: NEWSECRET234567/i);

    await userEvent.type(screen.getByLabelText("New authenticator code"), "222222");
    await userEvent.click(screen.getByText("Confirm"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/totp/reenroll/confirm",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ code: "222222" }) }),
    );
    expect(await screen.findByText("code0")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeDisabled();

    await userEvent.click(screen.getByLabelText(/i've saved these codes/i));
    expect(screen.getByText("Done")).toBeEnabled();
  });

  it("shows an error when the authenticator reset start is rejected", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(401, "invalid_code", "Incorrect verification code", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password for two-factor reset"), "old-password");
    await userEvent.type(screen.getByLabelText("Current authenticator code"), "000000");
    await userEvent.click(screen.getByText("Replace authenticator"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect verification code");
  });
});
