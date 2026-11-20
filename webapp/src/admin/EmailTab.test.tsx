import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import { EmailTab } from "./EmailTab";
import type { MailProvider, OutboxSummary } from "./useAdminMail";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const SMTP: MailProvider = {
  provider: "smtp2go",
  label: "SMTP2GO",
  available: true,
  is_active: false,
  fields: [
    { name: "sender", label: "Sender address", secret: false, required: true, value: "noreply@example.com", is_set: true },
    { name: "api_key", label: "API key", secret: true, required: true, value: null, is_set: true },
  ],
};
const OTHER: MailProvider = {
  provider: "other",
  label: "Other Mail",
  available: false,
  is_active: false,
  fields: [{ name: "token", label: "Token", secret: false, required: false, value: null, is_set: false }],
};
const SUMMARY: OutboxSummary = { pending: 2, sent_24h: 10, failed_24h: 1, last_error: null };

interface Backend {
  providers: MailProvider[] | Error;
  summary: OutboxSummary;
}

let backend: Backend;

function calls(method: string) {
  return vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === method);
}

function setup(next: Partial<Backend> = {}) {
  backend = { providers: [SMTP, OTHER], summary: SUMMARY, ...next };
  vi.mocked(apiFetch).mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/providers") && !init?.method) {
      if (backend.providers instanceof Error) throw backend.providers;
      return backend.providers;
    }
    if (path.endsWith("/outbox/summary")) return backend.summary;
    return SMTP;
  });
}

function renderTab() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <EmailTab />
    </QueryClientProvider>,
  );
}

describe("EmailTab", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("names the mail-actor container when no provider is declared", async () => {
    setup({ providers: [] });
    renderTab();
    expect(await screen.findByText(/No email provider has been declared yet/)).toHaveTextContent("mail-actor");
  });

  it("shows QueryError when the providers query fails", async () => {
    setup({ providers: new ApiError(500, "boom", "Providers broke", null) });
    renderTab();
    expect(await screen.findByText("Providers broke")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("says no provider is active and that email uses the built-in relay", async () => {
    setup();
    renderTab();
    expect(await screen.findByText("No provider is active. Email goes through the built-in mail relay.")).toBeInTheDocument();
  });

  it("names the active provider", async () => {
    setup({ providers: [{ ...SMTP, is_active: true }, OTHER] });
    renderTab();
    expect(await screen.findByText("Email goes through SMTP2GO.")).toBeInTheDocument();
  });

  it("draws the fields of the selected provider", async () => {
    setup();
    renderTab();
    expect(await screen.findByLabelText("Sender address (required)")).toHaveValue("noreply@example.com");
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "other");
    expect(screen.getByLabelText("Token")).toHaveValue("");
    expect(screen.queryByLabelText("Sender address (required)")).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Other Mail (not available)" })).toBeInTheDocument();
  });

  it("shows a secret as set, never its value, and Change reveals a password input", async () => {
    setup();
    renderTab();
    expect(await screen.findByText(/API key \(required\): set/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/New API key/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change" }));
    expect(screen.getByLabelText("New API key (required)")).toHaveAttribute("type", "password");
  });

  it("disables Save until something changes and sends only changed fields", async () => {
    setup();
    renderTab();
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    const sender = screen.getByLabelText("Sender address (required)");
    await userEvent.clear(sender);
    await userEvent.type(sender, "new@example.com");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(calls("PUT")).toHaveLength(1);
    expect(calls("PUT")[0][0]).toBe("/api/v1/admin/mail/providers/smtp2go");
    expect(JSON.parse(calls("PUT")[0][1]?.body as string)).toEqual({ settings: { sender: "new@example.com" } });
  });

  it("sends a secret only when one was typed", async () => {
    setup();
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: "Change" }));
    await userEvent.type(screen.getByLabelText("New API key (required)"), "s3cret");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(JSON.parse(calls("PUT")[0][1]?.body as string)).toEqual({ settings: { api_key: "s3cret" } });
  });

  it("shows the message of a field_required error", async () => {
    setup();
    renderTab();
    const sender = await screen.findByLabelText("Sender address (required)");
    await userEvent.clear(sender);
    vi.mocked(apiFetch).mockRejectedValueOnce(new ApiError(422, "field_required", "Sender address is required", null));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Sender address is required")).toBeInTheDocument();
  });

  it("activates a provider and names the one it replaces", async () => {
    setup({ providers: [{ ...SMTP }, { ...OTHER, available: true, is_active: true }] });
    renderTab();
    await screen.findByLabelText("Provider");
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "smtp2go");
    expect(screen.getByText(/replaces Other Mail/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Activate" }));
    expect(calls("POST").map(([path]) => path)).toEqual(["/api/v1/admin/mail/providers/smtp2go/activate"]);
  });

  it("disables Activate for an unavailable provider", async () => {
    setup();
    renderTab();
    await userEvent.selectOptions(await screen.findByLabelText("Provider"), "other");
    expect(screen.getByRole("button", { name: "Activate" })).toBeDisabled();
  });

  it("deactivates only after confirmation", async () => {
    setup({ providers: [{ ...SMTP, is_active: true }, OTHER] });
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: "Deactivate" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls("POST")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    const dialog = screen.getByRole("alertdialog");
    await userEvent.click(dialog.querySelector("button.bg-danger") as HTMLElement);
    expect(calls("POST").map(([path]) => path)).toEqual(["/api/v1/admin/mail/providers/smtp2go/deactivate"]);
  });

  it("shows the message of an activation error", async () => {
    setup();
    renderTab();
    const button = await screen.findByRole("button", { name: "Activate" });
    vi.mocked(apiFetch).mockRejectedValueOnce(new ApiError(409, "settings_incomplete", "Sender address is not set", null));
    await userEvent.click(button);
    expect(await screen.findByText("Sender address is not set")).toBeInTheDocument();
  });

  it("disables Send test email without an active provider", async () => {
    setup();
    renderTab();
    expect(await screen.findByRole("button", { name: "Send test email" })).toBeDisabled();
  });

  it("queues a test email and says it was queued, not sent", async () => {
    setup({ providers: [{ ...SMTP, is_active: true }, OTHER] });
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: "Send test email" }));
    expect(
      await screen.findByText("Test email queued. Check the inbox; delivery depends on the provider."),
    ).toBeInTheDocument();
    expect(calls("POST")[0][0]).toBe("/api/v1/admin/mail/test");
  });

  it.each([
    [409, "no_active_provider", "No provider is active"],
    [429, "too_many_tests", "Too many test emails"],
  ])("shows the %i test error message", async (status, code, message) => {
    setup({ providers: [{ ...SMTP, is_active: true }, OTHER] });
    renderTab();
    const button = await screen.findByRole("button", { name: "Send test email" });
    vi.mocked(apiFetch).mockRejectedValueOnce(new ApiError(status, code, message, null));
    await userEvent.click(button);
    expect(await screen.findByText(message)).toBeInTheDocument();
  });

  it("shows the outbox counts and the last error", async () => {
    setup({ summary: { pending: 2, sent_24h: 10, failed_24h: 1, last_error: "Connection refused" } });
    renderTab();
    expect(await screen.findByText("Pending: 2 · Sent (24 h): 10 · Failed (24 h): 1")).toBeInTheDocument();
    expect(screen.getByText("Last error: Connection refused")).toBeInTheDocument();
  });

  it("omits the last error line when there is none", async () => {
    setup();
    renderTab();
    await screen.findByText("Pending: 2 · Sent (24 h): 10 · Failed (24 h): 1");
    expect(screen.queryByText(/Last error/)).not.toBeInTheDocument();
  });
});
