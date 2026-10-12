import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { SourcesPage } from "./SourcesPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7/sources"]}>
        <Routes>
          <Route path="/sites/:siteId/sources" element={<SourcesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SourcesPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("lists existing sources", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      { id: 1, site_id: 7, kind: "energy", provider: "envoy", name: null, enabled: true, config: { host: "envoy.local" } },
    ]);
    renderPage();
    // "envoy" is also the Provider dropdown's default option in the create
    // form below, so this must be scoped to the sources list itself.
    expect(await within(screen.getByRole("list")).findByText("envoy")).toBeInTheDocument();
  });

  it("links back to the site's dashboard", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    renderPage();
    await screen.findByText("No sources yet.");
    expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/sites/7");
  });

  it("creates a source and confirms it without a collector key", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({
        id: 2,
        site_id: 7,
        kind: "weather",
        provider: "weatherlink_live",
        name: null,
        enabled: true,
        config: {},
      });
    renderPage();
    await screen.findByText("No sources yet.");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "weather");
    await userEvent.click(screen.getByText("Create source"));
    expect(await screen.findByText(/will start collecting/i)).toBeInTheDocument();
  });

  it("shows an error on failed source creation and does not show the collector key", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new ApiError(400, "invalid_provider", "Provider is invalid", null));
    renderPage();
    await screen.findByText("No sources yet.");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "weather");
    await userEvent.click(screen.getByText("Create source"));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid");
    expect(screen.queryByText(/will start collecting/i)).not.toBeInTheDocument();
  });

  it("limits the Provider dropdown to the options for the selected Kind", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    renderPage();
    await screen.findByText("No sources yet.");
    expect(screen.getByLabelText("Provider")).toHaveValue("envoy");
    expect(within(screen.getByLabelText("Provider")).getAllByRole("option")).toHaveLength(1);

    await userEvent.selectOptions(screen.getByLabelText("Kind"), "weather");
    expect(screen.getByLabelText("Provider")).toHaveValue("weatherlink_live");

    await userEvent.selectOptions(screen.getByLabelText("Kind"), "pricing");
    expect(screen.getByLabelText("Provider")).toHaveValue("amber");
  });

  it("creates a source with an optional label", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({
        id: 2,
        site_id: 7,
        kind: "energy",
        provider: "envoy",
        name: "Garage Envoy",
        enabled: true,
        config: {},
      });
    renderPage();
    await screen.findByText("No sources yet.");
    await userEvent.type(screen.getByPlaceholderText("Label (optional)"), "Garage Envoy");
    await userEvent.click(screen.getByText("Create source"));

    const postCall = vi
      .mocked(apiFetch)
      .mock.calls.find(([path, options]) => path === "/api/v1/sites/7/sources" && options?.method === "POST");
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      kind: "energy",
      provider: "envoy",
      name: "Garage Envoy",
      config: {},
    });
  });

  it("deletes a source that has no data", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([{ id: 1, site_id: 7, kind: "energy", provider: "envoy", name: null, enabled: true, config: {} }])
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce([]);
    renderPage();
    await userEvent.click(await screen.findByText("Delete"));
    await userEvent.click(screen.getByText("Confirm delete"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/1",
      expect.objectContaining({ method: "DELETE" }),
    );
    await screen.findByText("No sources yet.");
  });

  it("shows an error when delete is blocked by existing readings", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([{ id: 1, site_id: 7, kind: "energy", provider: "envoy", name: null, enabled: true, config: {} }])
      .mockRejectedValueOnce(
        new ApiError(409, "source_has_readings", "This source has existing readings and cannot be deleted. Disable it instead.", null),
      );
    renderPage();
    await userEvent.click(await screen.findByText("Delete"));
    await userEvent.click(screen.getByText("Confirm delete"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Disable it instead");
    expect(within(screen.getByRole("list")).getByText("envoy")).toBeInTheDocument();
  });

  it("saves an edited label on an existing source", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([{ id: 1, site_id: 7, kind: "energy", provider: "envoy", name: null, enabled: true, config: {} }])
      .mockResolvedValueOnce({ id: 1, site_id: 7, kind: "energy", provider: "envoy", name: "Garage Envoy", enabled: true, config: {} });
    renderPage();
    await within(screen.getByRole("list")).findByText("envoy");
    await userEvent.type(screen.getByLabelText("Label"), "Garage Envoy");
    await userEvent.click(screen.getByText("Save label"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Garage Envoy" }) }),
    );
  });

  it("disables an enabled source", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([{ id: 1, site_id: 7, kind: "energy", provider: "envoy", enabled: true, config: { host: "envoy.local" } }])
      .mockResolvedValueOnce({ id: 1, site_id: 7, kind: "energy", provider: "envoy", enabled: false, config: { host: "envoy.local" } })
      .mockResolvedValueOnce([{ id: 1, site_id: 7, kind: "energy", provider: "envoy", enabled: false, config: { host: "envoy.local" } }]);
    renderPage();
    await userEvent.click(await screen.findByText("Disable"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ enabled: false }) }),
    );
    expect(await screen.findByText("Enable")).toBeInTheDocument();
  });

  it("shows an error when toggling a source fails, and leaves its state unchanged", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([{ id: 1, site_id: 7, kind: "energy", provider: "envoy", enabled: true, config: { host: "envoy.local" } }])
      .mockRejectedValueOnce(new ApiError(500, "internal_error", "Could not update source", null));
    renderPage();
    await userEvent.click(await screen.findByText("Disable"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not update source");
    expect(screen.getByText("Disable")).toBeInTheDocument();
  });

  it("shows a guided envoy config form, prefilled, with serial_num read-only, and submits the edited fields", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([
        {
          id: 1,
          site_id: 7,
          kind: "energy",
          provider: "envoy",
          enabled: true,
          config: { host: "envoy.local", token: "old-token", serial_num: "123456789012" },
        },
      ])
      .mockResolvedValueOnce({
        id: 1,
        site_id: 7,
        kind: "energy",
        provider: "envoy",
        enabled: true,
        config: { host: "envoy.local", token: "new-token", serial_num: "123456789012" },
      });
    renderPage();
    await userEvent.click(await screen.findByText("Edit config"));

    expect(screen.getByLabelText("Host")).toHaveValue("envoy.local");
    expect(screen.getByLabelText("Token")).toHaveValue("old-token");
    // A local-API device token can reach the owner's hardware directly, so it
    // must never render as plain readable text (screen-share, shoulder-surf,
    // or a support screenshot would otherwise capture a live credential).
    expect(screen.getByLabelText("Token")).toHaveAttribute("type", "password");
    expect(screen.getByText("123456789012")).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Token"));
    await userEvent.type(screen.getByLabelText("Token"), "new-token");
    await userEvent.click(screen.getByText("Save config"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ config: { host: "envoy.local", token: "new-token", invert_consumption_ct: false } }),
      }),
    );
  });

  it("lets the user flag a reversed consumption CT clamp on the envoy config form", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([
        {
          id: 1,
          site_id: 7,
          kind: "energy",
          provider: "envoy",
          enabled: true,
          config: { host: "envoy.local", token: "old-token" },
        },
      ])
      .mockResolvedValueOnce({
        id: 1,
        site_id: 7,
        kind: "energy",
        provider: "envoy",
        enabled: true,
        config: { host: "envoy.local", token: "old-token", invert_consumption_ct: true },
      });
    renderPage();
    await userEvent.click(await screen.findByText("Edit config"));

    const checkbox = screen.getByLabelText("My consumption numbers are flipped (importing shows as negative)");
    expect(checkbox).not.toBeChecked();

    await userEvent.click(checkbox);
    await userEvent.click(screen.getByText("Save config"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          config: { host: "envoy.local", token: "old-token", invert_consumption_ct: true },
        }),
      }),
    );
  });

  it("shows a guided amber config form and submits the edited amber_site_id", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([
        { id: 2, site_id: 7, kind: "pricing", provider: "amber", enabled: true, config: { amber_site_id: "old-site" } },
      ])
      .mockResolvedValueOnce({
        id: 2,
        site_id: 7,
        kind: "pricing",
        provider: "amber",
        enabled: true,
        config: { amber_site_id: "new-site" },
      });
    renderPage();
    await userEvent.click(await screen.findByText("Edit config"));
    expect(screen.getByLabelText("Amber site ID")).toHaveValue("old-site");

    await userEvent.clear(screen.getByLabelText("Amber site ID"));
    await userEvent.type(screen.getByLabelText("Amber site ID"), "new-site");
    await userEvent.click(screen.getByText("Save config"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/2",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ config: { amber_site_id: "new-site" } }) }),
    );
  });

  it("shows a guided weatherlink_live config form and submits the edited host", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([
        { id: 3, site_id: 7, kind: "weather", provider: "weatherlink_live", enabled: true, config: { host: "weather.local" } },
      ])
      .mockResolvedValueOnce({
        id: 3,
        site_id: 7,
        kind: "weather",
        provider: "weatherlink_live",
        enabled: true,
        config: { host: "new-weather.local" },
      });
    renderPage();
    await userEvent.click(await screen.findByText("Edit config"));
    expect(screen.getByLabelText("Host")).toHaveValue("weather.local");

    await userEvent.clear(screen.getByLabelText("Host"));
    await userEvent.type(screen.getByLabelText("Host"), "new-weather.local");
    await userEvent.click(screen.getByText("Save config"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/3",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ config: { host: "new-weather.local" } }) }),
    );
  });

  it("falls back to a raw JSON editor for an unrecognized provider", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([
        { id: 4, site_id: 7, kind: "pricing", provider: "some_future_provider", enabled: true, config: { foo: "bar" } },
      ])
      .mockResolvedValueOnce({
        id: 4,
        site_id: 7,
        kind: "pricing",
        provider: "some_future_provider",
        enabled: true,
        config: { foo: "baz" },
      });
    renderPage();
    await userEvent.click(await screen.findByText("Edit config"));
    const textarea = screen.getByLabelText("Config (JSON)");
    expect(textarea).toHaveValue(JSON.stringify({ foo: "bar" }, null, 2));

    fireEvent.change(textarea, { target: { value: JSON.stringify({ foo: "baz" }) } });
    await userEvent.click(screen.getByText("Save config"));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/sources/4",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ config: { foo: "baz" } }) }),
    );
  });

  it("shows an error when saving config fails", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([
        { id: 1, site_id: 7, kind: "energy", provider: "envoy", enabled: true, config: { host: "envoy.local" } },
      ])
      .mockRejectedValueOnce(new ApiError(400, "invalid_config", "Config rejected", null));
    renderPage();
    await userEvent.click(await screen.findByText("Edit config"));
    await userEvent.click(screen.getByText("Save config"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Config rejected");
  });
});
