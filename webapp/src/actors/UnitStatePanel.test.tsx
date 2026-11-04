import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UnitStatePanel } from "./UnitStatePanel";
import type { ActorInstance, DeviceStatePayload } from "./types";

const NOW = "2026-11-05T03:00:00Z";

const state: DeviceStatePayload = {
  is_on: true, mode: "COOL", fan_mode: "AUTO", cool_setpoint_c: 24, heat_setpoint_c: 20,
  enabled_zones: { Lounge: true, "Bed 1": false }, indoor_temp_c: 25.5, outdoor_temp_c: 31, humidity_pct: 48,
  zone_temps_c: { Lounge: 25.5 }, updated_at: "2026-11-05T02:59:30Z", setpoint_min_c: 16, setpoint_max_c: 30,
};

function instance(overrides: Partial<ActorInstance> = {}): ActorInstance {
  return {
    id: 3, site_id: 7, provider: "actron_neo", name: "Lounge AC", enabled: true,
    capabilities: { version: 1, actions: [], readings: [] }, capabilities_updated_at: null,
    latest_readings: state, last_seen_at: "2026-11-05T02:59:30Z", auth_state: null, ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date(NOW) });
});
afterEach(() => vi.useRealTimers());

describe("UnitStatePanel", () => {
  it("shows power, mode, setpoint, temperatures and update time", () => {
    render(<UnitStatePanel instance={instance()} timezone="Australia/Sydney" />);
    expect(screen.getByRole("heading", { name: "Air conditioner", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Power").nextSibling).toHaveTextContent("On");
    expect(screen.getByText("Mode").nextSibling).toHaveTextContent("Cool");
    expect(screen.getByText("Cool setpoint").nextSibling).toHaveTextContent("24°C");
    expect(screen.queryByText("Heat setpoint")).not.toBeInTheDocument();
    expect(screen.getByText("Indoor").nextSibling).toHaveTextContent("25.5°C");
    expect(screen.getByText("Outdoor").nextSibling).toHaveTextContent("31°C");
    expect(screen.getByText("Humidity").nextSibling).toHaveTextContent("48%");
    expect(screen.getByText("Today 13:59")).toHaveAttribute("datetime", state.updated_at);
  });

  it("shows the heat setpoint in heat mode", () => {
    render(<UnitStatePanel instance={instance({ latest_readings: { ...state, mode: "HEAT" } })} timezone="UTC" />);
    expect(screen.getByText("Heat setpoint").nextSibling).toHaveTextContent("20°C");
  });

  it("lists each zone with on/off and temperature when known", () => {
    render(<UnitStatePanel instance={instance()} timezone="UTC" />);
    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual(["Lounge: On, 25.5°C", "Bed 1: Off"]);
  });

  it("shows Not updating after 2 minutes", () => {
    render(<UnitStatePanel instance={instance()} timezone="UTC" />);
    expect(screen.queryByText("Not updating")).not.toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(120_000); });
    const note = screen.getByText("Not updating");
    expect(note).toHaveAttribute("title", "The actor has not reported for more than 2 minutes.");
  });

  it("shows Not updating when the actor was never seen", () => {
    render(<UnitStatePanel instance={instance({ last_seen_at: null })} timezone="UTC" />);
    expect(screen.getByText("Not updating")).toBeInTheDocument();
  });

  it("shows No device state yet", () => {
    render(<UnitStatePanel instance={instance({ latest_readings: null })} timezone="UTC" />);
    expect(screen.getByText("No device state yet.")).toBeInTheDocument();
    expect(screen.queryByText("Power")).not.toBeInTheDocument();
  });

  it("handles missing optional fields", () => {
    const sparse = { ...state, indoor_temp_c: null, outdoor_temp_c: null, humidity_pct: null, zone_temps_c: {}, enabled_zones: {} };
    render(<UnitStatePanel instance={instance({ latest_readings: sparse })} timezone="UTC" />);
    expect(screen.queryByText("Indoor")).not.toBeInTheDocument();
    expect(screen.queryByText("Humidity")).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("offers no controls", () => {
    render(<UnitStatePanel instance={instance()} timezone="UTC" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
