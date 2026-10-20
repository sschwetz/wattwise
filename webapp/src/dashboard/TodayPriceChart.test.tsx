import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TodayPriceChart } from "./TodayPriceChart";
import type { PriceReading } from "./priceChart";
import { useTodayPrices } from "./useHistoricalData";

vi.mock("./useHistoricalData", () => ({ useTodayPrices: vi.fn() }));

const FIVE_MINUTES_MS = 300_000;
// A forecast interval comfortably after "now", on a 5-minute boundary.
const FORECAST_START = Math.ceil(Date.now() / FIVE_MINUTES_MS) * FIVE_MINUTES_MS + 3 * FIVE_MINUTES_MS;

function forecast(offsetIntervals: number, extra: Partial<PriceReading> = {}): PriceReading {
  return {
    ts: new Date(FORECAST_START + offsetIntervals * FIVE_MINUTES_MS).toISOString(),
    metric: "buy",
    value: 24,
    ...extra,
  };
}

function mockPrices(buy: PriceReading[]) {
  vi.mocked(useTodayPrices).mockImplementation(
    (_siteId, channel) => ({ data: channel === "buy" ? buy : [], isError: false }) as ReturnType<typeof useTodayPrices>,
  );
}

function renderChart() {
  return render(<TodayPriceChart siteId={1} zoom={{ range: null, set: vi.fn() }} />);
}

describe("TodayPriceChart instability", () => {
  beforeEach(() => {
    vi.mocked(useTodayPrices).mockReset();
  });

  it("shows the forecast range legend entry and the instability summary when Amber flags an interval", () => {
    mockPrices([forecast(0, { price_low: 20, price_high: 26, spike_status: "potential" }), forecast(1)]);
    renderChart();

    expect(screen.getByText("Forecast range")).toBeInTheDocument();
    expect(screen.getByText(/^Price instability expected /)).toBeInTheDocument();
  });

  it("shows the range legend but no summary when bands are narrow and nothing is flagged", () => {
    mockPrices([forecast(0, { price_low: 22, price_high: 26 }), forecast(1, { price_low: 22, price_high: 27 })]);
    renderChart();

    expect(screen.getByText("Forecast range")).toBeInTheDocument();
    expect(screen.queryByText(/Price instability/)).not.toBeInTheDocument();
  });

  it("shows neither when no forecast has a band or a flag", () => {
    mockPrices([forecast(0), forecast(1)]);
    renderChart();

    expect(screen.queryByText("Forecast range")).not.toBeInTheDocument();
    expect(screen.queryByText(/Price instability/)).not.toBeInTheDocument();
  });

  it("does not count the sell channel's band toward instability", () => {
    vi.mocked(useTodayPrices).mockImplementation(
      (_siteId, channel) =>
        ({
          data: channel === "sell" ? [forecast(0, { metric: "sell", price_low: -1, price_high: -60, spike_status: "spike" })] : [],
          isError: false,
        }) as ReturnType<typeof useTodayPrices>,
    );
    renderChart();

    expect(screen.queryByText(/Price instability/)).not.toBeInTheDocument();
  });
});
