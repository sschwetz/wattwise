import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AboutDialog } from "./AboutDialog";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const fetchMock = vi.fn();

function renderDialog(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AboutDialog onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

describe("AboutDialog", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset().mockResolvedValue([]);
    fetchMock.mockReset().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ generated_at: "x", texts: {}, packages: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("is a modal dialog named About WattWise", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog", { name: "About WattWise" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("closes on Escape, the Close button and the backdrop", async () => {
    const onClose = renderDialog();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByTestId("about-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("does not close when the panel itself is clicked", async () => {
    const onClose = renderDialog();
    await userEvent.click(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("switches tabs with the arrow keys", async () => {
    renderDialog();
    const containers = screen.getByRole("tab", { name: "Containers" });
    expect(containers).toHaveAttribute("aria-selected", "true");
    containers.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Licences" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Containers" })).toHaveAttribute("aria-selected", "true");
  });

  it("requests licences only after the Licences tab opens", async () => {
    renderDialog();
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/services");
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("tab", { name: "Licences" }));
    expect(await screen.findByRole("tabpanel")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("licences.json"));
  });

  it("keeps Tab focus inside the dialog", async () => {
    renderDialog();
    const dialog = screen.getByRole("dialog");
    for (let i = 0; i < 8; i++) {
      await userEvent.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    await userEvent.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
  });
});
