import { queryOptions, useQueries, useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { startOfDay } from "./dateRange";

export interface Reading {
  ts: string;
  metric: string;
  value: number;
}

/**
 * The energy metrics the Core API accepts, mirroring core_api.models.readings.EnergyMetric.
 * GET /sites/{id}/readings/energy requires exactly one of these per request — readings are
 * stored one row per (source_id, ts, metric), so there is no "all metrics" query.
 */
export const ENERGY_METRICS = [
  "production_w",
  "consumption_w",
  "grid_import_w",
  "grid_export_w",
  "battery_charge_w",
  "battery_discharge_w",
  "battery_soc_pct",
] as const;

export type EnergyMetric = (typeof ENERGY_METRICS)[number];

/**
 * The weather metrics the Core API accepts, mirroring core_api.models.readings.WeatherMetric.
 * Same one-metric-per-request contract as energy readings.
 */
export const WEATHER_METRICS = [
  "temp_c",
  "humidity_pct",
  "wind_speed_kph",
  "wind_direction_deg",
  "rain_mm",
  "solar_radiation_wm2",
  "barometric_pressure_hpa",
] as const;

export type WeatherMetric = (typeof WEATHER_METRICS)[number];

export const LIVE_POLL_MS = 60_000;
const LIVE_WINDOW_MS = 5 * 60_000;

function fetchLatest(siteId: number, path: string, params: Record<string, string>): Promise<Reading[]> {
  const end = new Date();
  const start = new Date(end.getTime() - LIVE_WINDOW_MS);
  const query = new URLSearchParams({ ...params, start: start.toISOString(), end: end.toISOString() });
  return apiFetch<Reading[]>(`/api/v1/sites/${siteId}/${path}?${query}`);
}

export interface LatestReadingTime {
  ts: string | null;
}

/** When the site's energy collectors last wrote a reading (null if never). */
export function useLatestEnergyTime(siteId: number) {
  return useQuery({
    queryKey: ["energy-latest", siteId],
    queryFn: () => apiFetch<LatestReadingTime>(`/api/v1/sites/${siteId}/readings/energy/latest`),
    refetchInterval: LIVE_POLL_MS,
  });
}

export interface LiveReadingsResult {
  data: Reading[];
  isLoading: boolean;
  isError: boolean;
}

function combineLiveResults(results: { data?: Reading[]; isLoading: boolean; isError: boolean }[]): LiveReadingsResult {
  return {
    data: results.flatMap((result) => result.data ?? []),
    isLoading: results.some((result) => result.isLoading),
    isError: results.some((result) => result.isError),
  };
}

/**
 * Fans out one request per energy metric (the backend takes a single required `metric`)
 * and merges the responses into one flat list of readings. `paused` stops the
 * 60 s refetch while the live stream is supplying the values.
 */
export function useLatestEnergyReadings(siteId: number, paused = false): LiveReadingsResult {
  const results = useQueries({
    queries: ENERGY_METRICS.map((metric) =>
      queryOptions({
        queryKey: ["energy-readings", siteId, "latest", metric],
        queryFn: () => fetchLatest(siteId, "readings/energy", { metric }),
        refetchInterval: paused ? false : LIVE_POLL_MS,
      }),
    ),
  });
  return combineLiveResults(results);
}

/**
 * rain_mm is the rain since the previous poll, so its latest reading is
 * almost always 0. It is shown as a running total by useTodayRain instead.
 */
const LATEST_WEATHER_METRICS = WEATHER_METRICS.filter((metric) => metric !== "rain_mm");

/** Same fan-out shape as useLatestEnergyReadings, one request per weather metric. */
export function useLatestWeatherReadings(siteId: number): LiveReadingsResult {
  const results = useQueries({
    queries: LATEST_WEATHER_METRICS.map((metric) =>
      queryOptions({
        queryKey: ["weather-readings", siteId, "latest", metric],
        queryFn: () => fetchLatest(siteId, "readings/weather", { metric }),
        refetchInterval: LIVE_POLL_MS,
      }),
    ),
  });
  return combineLiveResults(results);
}

/** Total rain (mm, one decimal place) since local midnight; null when no rain reading exists yet today. */
export function useTodayRain(siteId: number) {
  return useQuery({
    queryKey: ["weather-readings", siteId, "today-rain"],
    queryFn: async (): Promise<number | null> => {
      const end = new Date();
      const params = new URLSearchParams({
        metric: "rain_mm",
        start: startOfDay(end).toISOString(),
        end: end.toISOString(),
      });
      const readings = await apiFetch<Reading[]>(`/api/v1/sites/${siteId}/readings/weather?${params}`);
      if (readings.length === 0) {
        return null;
      }
      const total = readings.reduce((sum, reading) => sum + reading.value, 0);
      return Math.round(total * 10) / 10;
    },
    refetchInterval: LIVE_POLL_MS,
  });
}

export type PriceChannel = "buy" | "sell";

export interface CurrentPriceResult {
  data: Reading | undefined;
  isError: boolean;
}

/**
 * The backend returns price readings sorted ascending by ts (core_api.routers.query.get_prices),
 * so the latest value is simply the last element of the response.
 */
export function useCurrentPrice(siteId: number, channel: PriceChannel): CurrentPriceResult {
  const result = useQuery(
    queryOptions({
      queryKey: ["current-price", siteId, channel],
      queryFn: () => fetchLatest(siteId, "prices", { channel }),
      refetchInterval: LIVE_POLL_MS,
    }),
  );
  return { data: result.data?.at(-1), isError: result.isError };
}
