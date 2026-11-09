import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ContainersTab } from "./ContainersTab";
import { type ServiceStatus, useServices } from "./useServices";

vi.mock("./useServices");

const NAMES = [
  "core-api",
  "webapp",
  "envoy-collector",
  "weatherlink-local-collector",
  "amber-pricing-collector",
  "local-pricing-collector",
  "actron-neo-actor",
];

function services(overrides: Partial<ServiceStatus> = {}, name = "core-api"): ServiceStatus[] {
  const rest = NAMES.filter((n) => n !== name).map((service) => ({
    service,
    commit: "abc1234",
    last_reported_at: "2026-11-09T01:00:00Z",
    reporting: true,
  }));
  return [{ service: name, commit: "abc1234", last_reported_at: "2026-11-09T01:00:00Z", reporting: true, ...overrides }, ...rest];
}

function mockServices(data: ServiceStatus[] | undefined, extra: object = {}) {
  vi.mocked(useServices).mockReturnValue({ data, ...extra } as ReturnType<typeof useServices>);
}

describe("ContainersTab", () => {
  beforeEach(() => vi.mocked(useServices).mockReset());

  it("renders every service in order", () => {
    mockServices(services());
    render(<ContainersTab />);
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((row) => row.firstElementChild?.textContent)).toEqual(NAMES);
  });

  it("links the commit of a reporting service and shows when it reported", () => {
    mockServices(services());
    render(<ContainersTab />);
    const [link] = screen.getAllByRole("link", { name: "abc1234" });
    expect(link).toHaveAttribute("href", "https://github.com/sschwetz/wattwise/commit/abc1234");
    expect(screen.getAllByText(/^last reported .*2026/)).toHaveLength(NAMES.length);
  });

  it("shows plain text when the commit has no link", () => {
    mockServices(services({ commit: "unknown" }));
    render(<ContainersTab />);
    expect(screen.getByText("unknown")).not.toBeInstanceOf(HTMLAnchorElement);
    expect(screen.queryByRole("link", { name: "unknown" })).toBeNull();
  });

  it("shows a non-reporting service with its last commit and time", () => {
    mockServices(services({ reporting: false }));
    render(<ContainersTab />);
    expect(screen.getByText("not reporting")).toBeInTheDocument();
    expect(screen.getAllByText(/^last reported .*2026/)).toHaveLength(NAMES.length);
    expect(screen.getAllByText("abc1234")).toHaveLength(NAMES.length);
  });

  it("shows never reported and a dash when nothing was received", () => {
    mockServices(services({ reporting: false, commit: null, last_reported_at: null }));
    render(<ContainersTab />);
    expect(screen.getByText("not reporting")).toBeInTheDocument();
    expect(screen.getByText("never reported")).toBeInTheDocument();
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("keeps showing loaded data when a refetch fails", () => {
    mockServices(services(), { isError: true });
    render(<ContainersTab />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("core-api")).toBeInTheDocument();
  });

  it("shows an error when the request fails and refetches on Retry", async () => {
    const refetch = vi.fn();
    mockServices(undefined, { isError: true, error: new Error("boom"), refetch });
    render(<ContainersTab />);
    expect(screen.getByRole("alert")).toHaveTextContent(/container status|boom/);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
