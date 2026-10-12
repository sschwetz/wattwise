import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiFetchBlob } from "../api/client";
import { AccountPage } from "./AccountPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn(), apiFetchBlob: vi.fn() };
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
    vi.mocked(apiFetchBlob).mockReset();
  });

  it("shows no key set yet when none is configured", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false });
    renderPage();
    expect(await screen.findByText(/no amber api key set yet/i)).toBeInTheDocument();
  });

  it("shows the key is configured when one already exists", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: true });
    renderPage();
    expect(await screen.findByText(/amber api key is configured/i)).toBeInTheDocument();
  });

  it("masks the Amber API key input", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);
    expect(screen.getByLabelText("Amber API key")).toHaveAttribute("type", "password");
  });

  it("saves a new Amber API key", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: true })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: true });
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    const amberKeyInput = screen.getByLabelText("Amber API key");
    await userEvent.type(amberKeyInput, "psk_my_secret_key");
    await userEvent.click(within(amberKeyInput.closest("form")!).getByText("Save"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ amber_api_key: "psk_my_secret_key" }) }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("shows an error when saving fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(422, "invalid_key", "amber_api_key must not be empty", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    const amberKeyInput = screen.getByLabelText("Amber API key");
    await userEvent.type(amberKeyInput, "x");
    await userEvent.click(within(amberKeyInput.closest("form")!).getByText("Save"));

    expect(await screen.findByRole("alert")).toHaveTextContent("amber_api_key must not be empty");
  });

  it("changes the password when current password and TOTP code are correct", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
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
    vi.mocked(apiFetch).mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false });
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
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
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
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
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
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
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
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
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
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(401, "invalid_code", "Incorrect verification code", null));
    renderPage();
    await screen.findByText(/no amber api key set yet/i);

    await userEvent.type(screen.getByLabelText("Current password for two-factor reset"), "old-password");
    await userEvent.type(screen.getByLabelText("Current authenticator code"), "000000");
    await userEvent.click(screen.getByText("Replace authenticator"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect verification code");
  });

  it("shows the current display name", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: "Ada Lovelace",
      has_avatar: false, has_amber_api_key: false,
    });
    renderPage();
    expect(await screen.findByText(/current name: ada lovelace/i)).toBeInTheDocument();
  });

  it("shows a placeholder when no display name is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_avatar: false, has_amber_api_key: false,
    });
    renderPage();
    expect(await screen.findByText(/current name: not set/i)).toBeInTheDocument();
  });

  it("saves a new display name", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: "Ada Lovelace", has_avatar: false, has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: "Ada Lovelace", has_avatar: false, has_amber_api_key: false });
    renderPage();
    await screen.findByText(/current name: not set/i);

    const displayNameInput = screen.getByLabelText("Display name");
    await userEvent.type(displayNameInput, "Ada Lovelace");
    await userEvent.click(within(displayNameInput.closest("form")!).getByRole("button", { name: "Save" }));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/profile",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ display_name: "Ada Lovelace" }) }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("uploads a new avatar", async () => {
    // The upload response flips has_avatar to true, which re-enables
    // useAvatarImage and triggers an apiFetchBlob call -- mock that too, or
    // the query's fetch rejects with "apiFetchBlob is not a function".
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false });
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    const file = new File(["fake-bytes"], "avatar.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), file);

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/avatar",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an alert when the server rejects the uploaded avatar", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(400, "invalid_file_type", "Avatar must be a JPEG, PNG, or WebP image", null));
    renderPage();
    await screen.findByText(/current name: not set/i);

    const file = new File(["fake-bytes"], "avatar.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), file);

    expect(await screen.findByRole("alert")).toHaveTextContent("Avatar must be a JPEG, PNG, or WebP image");
  });

  it("rejects an oversized file before calling the API", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_avatar: false,
      has_amber_api_key: false,
    });
    renderPage();
    await screen.findByText(/current name: not set/i);

    const oversized = new File([new Uint8Array(2 * 1024 * 1024 + 1)], "avatar.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), oversized);

    expect(await screen.findByRole("alert")).toHaveTextContent(/2mb or smaller/i);
    expect(apiFetch).toHaveBeenCalledTimes(1); // only the initial useMe call
  });

  it("rejects a disallowed file type before calling the API", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_avatar: false,
      has_amber_api_key: false,
    });
    renderPage();
    await screen.findByText(/current name: not set/i);

    // userEvent.upload defaults to filtering files against the input's accept
    // attribute (mirroring a real OS file picker) -- disable that here, since
    // this test exists specifically to verify the component's own check, not
    // the browser's.
    const user = userEvent.setup({ applyAccept: false });
    const wrongType = new File(["hello"], "notes.txt", { type: "text/plain" });
    await user.upload(screen.getByLabelText("Upload avatar"), wrongType);

    expect(await screen.findByRole("alert")).toHaveTextContent(/jpeg, png, or webp/i);
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it("shows a Remove button only when an avatar is set, and removes it", async () => {
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false });
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/avatar",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("does not show a Remove button when no avatar is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_avatar: false,
      has_amber_api_key: false,
    });
    renderPage();
    await screen.findByText(/current name: not set/i);

    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("shows an alert when removing the avatar fails", async () => {
    const { ApiError } = await import("../api/client");
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(500, "internal_error", "An internal error occurred", null));
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("An internal error occurred");
  });

  it("keeps the upload control focusable for keyboard users (not display:none)", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_avatar: false,
      has_amber_api_key: false,
    });
    renderPage();
    await screen.findByText(/current name: not set/i);

    const input = screen.getByLabelText("Upload avatar");
    expect(input.className).not.toContain("hidden");
    expect(input.className).toContain("sr-only");
  });

  it("clears the upload error once a later remove succeeds", async () => {
    const { ApiError } = await import("../api/client");
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(400, "invalid_file_type", "Avatar must be a JPEG, PNG, or WebP image", null))
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: false, has_amber_api_key: false });
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    const file = new File(["fake-bytes"], "avatar.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), file);
    expect(await screen.findByRole("alert")).toHaveTextContent("Avatar must be a JPEG, PNG, or WebP image");

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("clears the remove error once a later upload succeeds", async () => {
    const { ApiError } = await import("../api/client");
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(500, "internal_error", "An internal error occurred", null))
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false });
    vi.mocked(apiFetchBlob)
      .mockResolvedValueOnce(new Blob(["old-bytes"]))
      .mockResolvedValueOnce(new Blob(["new-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("An internal error occurred");

    const file = new File(["fake-bytes"], "avatar.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("Upload avatar"), file);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("shows the remove error instead of a stale client-side pre-check error", async () => {
    const { ApiError } = await import("../api/client");
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_avatar: true, has_amber_api_key: false })
      .mockRejectedValueOnce(new ApiError(500, "internal_error", "An internal error occurred", null));
    vi.mocked(apiFetchBlob).mockResolvedValueOnce(new Blob(["fake-bytes"]));
    renderPage();
    await screen.findByText(/current name: not set/i);

    const user = userEvent.setup({ applyAccept: false });
    const wrongType = new File(["hello"], "notes.txt", { type: "text/plain" });
    await user.upload(screen.getByLabelText("Upload avatar"), wrongType);
    expect(await screen.findByRole("alert")).toHaveTextContent(/jpeg, png, or webp/i);

    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("An internal error occurred");
  });
});
