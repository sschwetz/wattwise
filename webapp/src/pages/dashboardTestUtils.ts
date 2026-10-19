import { vi } from "vitest";
import { ApiError, apiFetch } from "../api/client";
import type { Reading } from "../dashboard/useLiveReadings";

export function queryParams(path: string): URLSearchParams {
  return new URLSearchParams(path.split("?")[1] ?? "");
}

/**
 * The real Core API (GET /sites/{id}/readings/energy, /readings/weather, /prices) each take
 * a single required metric/channel plus `start`/`end`, and return only those rows. These
 * mocks reproduce that contract: every readings response is filtered down to what was asked.
 */
export function mockApi(
  readingsByMetric: Record<string, Reading[]>,
  cost?: Record<string, unknown>,
  weatherByMetric?: Record<string, Reading[]>,
  priceReadingsByChannel?: Record<string, Reading[]>,
  costSeries?: Record<string, unknown>[],
  // null (the default) means "no active billing cycle" -- the real backend's
  // 404 for that case, modeled here as a resolved null so most tests (which
  // don't care about the billing-period tile) get a tile that renders nothing.
  billingPeriodCost: Record<string, unknown> | null = null,
  // Same "null means 404 / nothing active" modeling as billingPeriodCost
  // above, for the dashboard heading's demand-window notice.
  activeDemandWindow: Record<string, unknown> | null = null,
  // The current billing period's start/end; null models the 404 for no active cycle.
  billingBounds: { start: string; end: string } | null = null,
) {
  vi.mocked(apiFetch).mockImplementation((path: string) => {
    if (path.endsWith("/billing-period")) {
      return billingBounds
        ? Promise.resolve(billingBounds)
        : Promise.reject(new ApiError(404, "no_active_billing_cycle", "none", null));
    }
    if (path.includes("/readings/energy")) {
      const metric = queryParams(path).get("metric");
      return Promise.resolve(metric ? (readingsByMetric[metric] ?? []) : []);
    }
    if (path.includes("/readings/weather")) {
      const metric = queryParams(path).get("metric");
      return Promise.resolve(metric ? (weatherByMetric?.[metric] ?? []) : []);
    }
    if (path.includes("/prices")) {
      const channel = queryParams(path).get("channel");
      return Promise.resolve(channel ? (priceReadingsByChannel?.[channel] ?? []) : []);
    }
    if (path.includes("/cost/series")) {
      return Promise.resolve(costSeries ?? []);
    }
    // Checked before the generic "/cost" branch below, since that path also
    // includes the substring "/cost".
    if (path.includes("/cost/billing-period")) {
      return Promise.resolve(billingPeriodCost);
    }
    if (path.includes("/cost")) {
      return Promise.resolve(cost ?? null);
    }
    if (path.includes("/demand-window/active")) {
      return Promise.resolve(activeDemandWindow);
    }
    return Promise.resolve([]);
  });
}

export function energyCallPaths(): string[] {
  return vi
    .mocked(apiFetch)
    .mock.calls.map(([path]) => path as string)
    .filter((path) => path.includes("/readings/energy"));
}

export function weatherCallPaths(): string[] {
  return vi
    .mocked(apiFetch)
    .mock.calls.map(([path]) => path as string)
    .filter((path) => path.includes("/readings/weather"));
}

export function pricePaths(): string[] {
  return vi
    .mocked(apiFetch)
    .mock.calls.map(([path]) => path as string)
    .filter((path) => path.includes("/prices"));
}

/** The live grid-price tile and the today's-pricing-chart hit the identical
 * `/prices?channel=...` endpoint shape, distinguishable only by how wide a
 * span they request: the live tile polls a few minutes back, the chart (like
 * the history views) spans a full calendar day or more. */
export function requestSpanMs(path: string): number | null {
  const params = queryParams(path);
  const start = params.get("start");
  const end = params.get("end");
  return start && end ? Date.parse(end) - Date.parse(start) : null;
}

export const LIVE_WINDOW_SPAN_THRESHOLD_MS = 60 * 60_000;
