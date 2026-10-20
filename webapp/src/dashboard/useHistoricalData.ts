import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../api/client";
import { LIVE_POLL_MS, type EnergyMetric, type PriceChannel, type Reading } from "./useLiveReadings";
import type { PriceReading } from "./priceChart";

export interface CostSummary {
  total_cost_c: number;
  total_import_kwh: number;
  total_export_kwh: number;
  minutes_missing_price: number;
}

export interface BillingPeriodCostSummary extends CostSummary {
  monthly_fee_discounts_c: number;
}

export interface DateRange {
  from: Date;
  to: Date;
}

export function useHistoricalEnergyReadings(siteId: number, range: DateRange, metric: EnergyMetric) {
  const params = new URLSearchParams({
    metric,
    start: range.from.toISOString(),
    end: range.to.toISOString(),
  });
  return useQuery({
    queryKey: [
      "energy-readings",
      siteId,
      "history",
      metric,
      range.from.toISOString(),
      range.to.toISOString(),
    ],
    queryFn: () => apiFetch<Reading[]>(`/api/v1/sites/${siteId}/readings/energy?${params}`),
  });
}

export function useCost(siteId: number, range: DateRange, method: "net" | "gross") {
  const params = new URLSearchParams({
    start: range.from.toISOString(),
    end: range.to.toISOString(),
    method,
  });
  return useQuery({
    queryKey: ["cost", siteId, range.from.toISOString(), range.to.toISOString(), method],
    queryFn: () => apiFetch<CostSummary>(`/api/v1/sites/${siteId}/cost?${params}`),
  });
}

/**
 * Today's running cost, independent of the history chart's own date-range
 * selector -- a live tile, not a view into whatever period the chart happens
 * to be showing. Like the live readings hooks, "now" is computed fresh inside
 * the queryFn on every poll (not passed in as a prop), so the window grows
 * over the course of the day instead of freezing at mount time.
 *
 * Requests prorate_fees=false: a live tile is meant to read as "what's
 * committed today", so it shows the day's service/monthly fee in full right
 * away rather than core-api's default smoothly-ticking proration (which the
 * history card's own "Cost" figure uses instead, via useCost below).
 */
export function useTodayCost(siteId: number, method: "net" | "gross") {
  return useQuery({
    queryKey: ["cost", siteId, "today", method],
    queryFn: () => {
      const end = new Date();
      const start = new Date(end);
      start.setHours(0, 0, 0, 0);
      const params = new URLSearchParams({
        start: start.toISOString(),
        end: end.toISOString(),
        method,
        prorate_fees: "false",
      });
      return apiFetch<CostSummary>(`/api/v1/sites/${siteId}/cost?${params}`);
    },
    refetchInterval: LIVE_POLL_MS,
  });
}

/**
 * The running cost/credit for the billing period "now" falls inside, from its
 * start up to now -- the billing-cycle analogue of useTodayCost above. The
 * backend 404s (code "no_active_billing_cycle") when no billing cycle is
 * currently active for the site; callers treat that as "nothing to show",
 * not an error.
 */
export function useBillingPeriodCost(siteId: number, method: "net" | "gross") {
  return useQuery({
    queryKey: ["cost", siteId, "billing-period", method],
    queryFn: () => {
      const params = new URLSearchParams({ method });
      return apiFetch<BillingPeriodCostSummary>(`/api/v1/sites/${siteId}/cost/billing-period?${params}`);
    },
    refetchInterval: LIVE_POLL_MS,
  });
}

interface BillingPeriodBounds {
  start: string;
  end: string;
}

/** Start and end of the current billing period, or null when the site has no
 * active billing cycle (404). Other errors surface so they are not mistaken
 * for "no billing period option to offer". */
export function useBillingPeriodBounds(siteId: number) {
  return useQuery({
    queryKey: ["billing-period", siteId],
    queryFn: async (): Promise<DateRange | null> => {
      try {
        const bounds = await apiFetch<BillingPeriodBounds>(`/api/v1/sites/${siteId}/billing-period`);
        return { from: new Date(bounds.start), to: new Date(bounds.end) };
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          return null;
        }
        throw err;
      }
    },
    staleTime: 5 * 60_000,
  });
}

/**
 * Today's buy/sell price readings, actual and forecast together, independent
 * of the history chart's own date-range selector -- same "always today, not
 * whatever the selector shows" reasoning as useTodayCost above. The backend
 * doesn't flag which rows are forecast-origin (see core-api's PriceReading --
 * a forecast row is overwritten in place once the real actual value lands),
 * but that's fine here: a row timestamped later than "now" can only be a
 * forecast, since nothing else could have produced it yet. It does flag
 * forecast rows that fell back to perKwh (`forecast_fallback`).
 */
export function useTodayPrices(siteId: number, channel: PriceChannel) {
  return useQuery({
    queryKey: ["prices", siteId, "today", channel],
    queryFn: () => {
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      const end = new Date(start);
      end.setDate(end.getDate() + 1);
      const params = new URLSearchParams({ channel, start: start.toISOString(), end: end.toISOString() });
      return apiFetch<PriceReading[]>(`/api/v1/sites/${siteId}/prices?${params}`);
    },
    refetchInterval: LIVE_POLL_MS,
  });
}

export type CostBucket = "5min" | "15min" | "hour" | "6hour";

export interface CostSeriesPoint {
  bucket_start: string;
  cost_c: number;
}

export function useCostSeries(siteId: number, range: DateRange, method: "net" | "gross", bucket: CostBucket) {
  const params = new URLSearchParams({
    start: range.from.toISOString(),
    end: range.to.toISOString(),
    method,
    bucket,
  });
  return useQuery({
    queryKey: ["cost-series", siteId, range.from.toISOString(), range.to.toISOString(), method, bucket],
    queryFn: () => apiFetch<CostSeriesPoint[]>(`/api/v1/sites/${siteId}/cost/series?${params}`),
  });
}
