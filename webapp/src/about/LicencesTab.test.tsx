import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LicencesTab } from "./LicencesTab";
import type { LicenceDocument } from "./useLicences";

const doc: LicenceDocument = {
  generated_at: "2026-11-09T00:00:00Z",
  texts: { t1: "MIT text body", t2: "Apache text body" },
  packages: [
    { name: "react", version: "19.0.0", ecosystem: "npm", license: "MIT", text_id: "t1" },
    { name: "fastapi", version: "0.110.0", ecosystem: "python", license: "Apache-2.0", text_id: "t2" },
    { name: "mystery", version: "1.0.0", ecosystem: "npm", license: "Custom", text_id: null },
  ],
};

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LicencesTab />
    </QueryClientProvider>,
  );
}

describe("LicencesTab", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(doc) }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists packages and selects the first", async () => {
    renderTab();
    expect(await screen.findByRole("option", { name: "react 19.0.0" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(screen.getByText("MIT text body")).toBeInTheDocument();
  });

  it("makes the licence text focusable and named", async () => {
    renderTab();
    const text = await screen.findByLabelText("Licence text for react 19.0.0");
    expect(text).toHaveAttribute("tabindex", "0");
    expect(text).toHaveTextContent("MIT text body");
  });

  it("shows the text of a selected package", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("option", { name: "fastapi 0.110.0" }));
    expect(screen.getByText("Apache text body")).toBeInTheDocument();
  });

  it("narrows the list with the filter and selects the first match", async () => {
    renderTab();
    await screen.findByRole("option", { name: "react 19.0.0" });
    await userEvent.type(screen.getByRole("textbox", { name: /filter/i }), "fast");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option", { name: "fastapi 0.110.0" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Apache text body")).toBeInTheDocument();
  });

  it("falls back when a package has no text", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("option", { name: "mystery 1.0.0" }));
    expect(screen.getByText("No licence text available.")).toBeInTheDocument();
  });

  it("moves the selection with arrow keys", async () => {
    renderTab();
    const first = await screen.findByRole("option", { name: "react 19.0.0" });
    expect(first).toBeInTheDocument();
    screen.getByRole("listbox").focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { name: "fastapi 0.110.0" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("option", { name: "mystery 1.0.0" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("option", { name: "react 19.0.0" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows an error when the fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }));
    renderTab();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
