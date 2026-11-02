import { skipToken, useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../api/client";
import { LIVE_POLL_MS, type EnergyMetric, type PriceChannel, type Reading } from "./useLiveReadings";
import type { PriceReading } from "./priceChart";
import { dayRangeOffset, startOfDay } from "./dateRange";

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

/** One minute at N watts is N/60000 kWh -- matches core_api.cost.MINUTES_TO_KWH. */
const MINUTES_TO_KWH = 1 / 60_000;

/** Total solar production (kWh) since local midnight, summed from the
 * per-minute production_w readings. Null when no reading exists yet today. */
export function useTodayProductionKwh(siteId: number) {
  return useQuery({
    queryKey: ["energy-readings", siteId, "today-production-kwh"],
    queryFn: async (): Promise<number | null> => {
      const end = new Date();
      const params = new URLSearchParams({
        metric: "production_w",
        start: startOfDay(end).toISOString(),
        end: end.toISOString(),
      });
      const readings = await apiFetch<Reading[]>(`/api/v1/sites/${siteId}/readings/energy?${params}`);
      if (readings.length === 0) {
        return null;
      }
      return readings.reduce((sum, reading) => sum + reading.value, 0) * MINUTES_TO_KWH;
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
export function useTodayPrices(siteId: number, channel: PriceChannel, dayOffset = 0) {
  return useQuery({
    queryKey: ["prices", siteId, "today", channel, dayOffset],
    queryFn: () => {
      const { from, to } = dayRangeOffset(dayOffset);
      const params = new URLSearchParams({ channel, start: from.toISOString(), end: to.toISOString() });
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

export type TotalsBucket = "day" | "hour" | "minute";

/** One day, hour or minute. Average prices are null on days and the demand
 * charge is null on hours and minutes. */
export interface TotalsRow {
  start: string;
  date: string;
  hours: number;
  cost_c: number;
  avg_import_price_c: number | null;
  avg_export_price_c: number | null;
  production_kwh: number;
  consumption_kwh: number;
  solar_to_battery_kwh: number;
  grid_to_battery_kwh: number;
  grid_import_kwh: number;
  grid_export_kwh: number;
  battery_charge_kwh: number;
  battery_discharge_kwh: number;
  battery_soc_pct: number | null;
  demand_charge_c: number | null;
  demand_charge_estimated: boolean;
}

type RawTotalsRow = Record<keyof TotalsRow, string | number | boolean | null>;

const nullableNumber = (value: string | number | boolean | null) => (value === null ? null : Number(value));

/** The API serializes decimals as strings, so every numeric field is converted here. */
function parseTotalsRow(row: RawTotalsRow): TotalsRow {
  return {
    start: String(row.start),
    date: String(row.date),
    hours: Number(row.hours),
    cost_c: Number(row.cost_c),
    avg_import_price_c: nullableNumber(row.avg_import_price_c),
    avg_export_price_c: nullableNumber(row.avg_export_price_c),
    production_kwh: Number(row.production_kwh),
    consumption_kwh: Number(row.consumption_kwh),
    solar_to_battery_kwh: Number(row.solar_to_battery_kwh),
    grid_to_battery_kwh: Number(row.grid_to_battery_kwh),
    grid_import_kwh: Number(row.grid_import_kwh),
    grid_export_kwh: Number(row.grid_export_kwh),
    battery_charge_kwh: Number(row.battery_charge_kwh),
    battery_discharge_kwh: Number(row.battery_discharge_kwh),
    battery_soc_pct: nullableNumber(row.battery_soc_pct),
    demand_charge_c: nullableNumber(row.demand_charge_c),
    demand_charge_estimated: Boolean(row.demand_charge_estimated),
  };
}

async function fetchTotals(
  siteId: number,
  range: DateRange,
  method: "net" | "gross",
  bucket: TotalsBucket,
): Promise<TotalsRow[]> {
  const params = new URLSearchParams({
    start: range.from.toISOString(),
    end: range.to.toISOString(),
    method,
    bucket,
  });
  const rows = await apiFetch<RawTotalsRow[]>(`/api/v1/sites/${siteId}/totals?${params}`);
  return rows.map(parseTotalsRow);
}

/** One row per day, hour or minute in `range`. Stays idle until `range` is known. */
export function useTotals(siteId: number, range: DateRange | undefined, method: "net" | "gross", bucket: TotalsBucket) {
  return useQuery({
    queryKey: ["totals", siteId, range?.from.toISOString(), range?.to.toISOString(), method, bucket],
    queryFn: range ? () => fetchTotals(siteId, range, method, bucket) : skipToken,
    // Keeps the table mounted while another month, day or hour loads at the
    // same level. Moving to another level shows nothing until its rows arrive.
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[5] === bucket ? previous : undefined),
  });
}
