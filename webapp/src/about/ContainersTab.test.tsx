import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ContainersTab } from "./ContainersTab";
import { type ServiceStatus, useServices } from "./useServices";

vi.mock("./useServices");

const NAMES = ["core-api", "webapp", "worker", "amber", "envoy", "actron", "mqtt"];

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

  it("renders every service", () => {
    mockServices(services());
    render(<ContainersTab />);
    for (const name of NAMES) expect(screen.getByText(name)).toBeInTheDocument();
  });

  it("links the commit of a reporting service", () => {
    mockServices(services());
    render(<ContainersTab />);
    const [link] = screen.getAllByRole("link", { name: "abc1234" });
    expect(link).toHaveAttribute("href", "https://github.com/sschwetz/wattwise/commit/abc1234");
  });

  it("shows a non-reporting service with its last time", () => {
    mockServices(services({ reporting: false }));
    render(<ContainersTab />);
    expect(screen.getByText("not reporting")).toBeInTheDocument();
    expect(screen.getByText(/^last reported /)).toBeInTheDocument();
  });

  it("shows never reported when there is no time", () => {
    mockServices(services({ reporting: false, commit: null, last_reported_at: null }));
    render(<ContainersTab />);
    expect(screen.getByText("not reporting")).toBeInTheDocument();
    expect(screen.getByText("never reported")).toBeInTheDocument();
  });

  it("shows an error when the request fails", () => {
    mockServices(undefined, { isError: true });
    render(<ContainersTab />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
