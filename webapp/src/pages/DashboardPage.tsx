import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useActiveDemandWindow } from "../billing/useBilling";
import { compassDirection } from "../dashboard/compass";
import { ErrorAlert, ResetZoomButton } from "../dashboard/chartParts";
import { DataFreshness } from "../dashboard/DataFreshness";
import { clipToDay, todayRange, zoomRangeFor } from "../dashboard/dateRange";
import { formatTooltipValue } from "../dashboard/historyChart";
import { HistoryChart } from "../dashboard/GridCostChart";
import { TODAY_CHARTS_SYNC_ID } from "../dashboard/chartStyle";
import { useGridCostSeries } from "../dashboard/useGridCostSeries";
import { priceMagnitude } from "../dashboard/priceChart";
import { signAccent } from "../dashboard/signColors";
import { Tile } from "../dashboard/Tile";
import { TodayPriceChart } from "../dashboard/TodayPriceChart";
import { type ChartZoom } from "../dashboard/useChartZoom";
import { type DateRange, useBillingPeriodCost, useTodayCost } from "../dashboard/useHistoricalData";
import {
  type PriceChannel,
  type Reading,
  useCurrentPrice,
  useLatestEnergyReadings,
  useLatestWeatherReadings,
} from "../dashboard/useLiveReadings";
import { averageLiveReadings } from "../dashboard/liveAverages";

const TILE_LABELS: Record<string, string> = {
  production_w: "Production",
  consumption_w: "Consumption",
  grid_import_w: "Grid import",
  grid_export_w: "Grid export",
  battery_charge_w: "Battery charge",
  battery_discharge_w: "Battery discharge",
  battery_soc_pct: "Battery",
};

const TILE_UNITS: Record<string, string> = {
  production_w: "W",
  consumption_w: "W",
  grid_import_w: "W",
  grid_export_w: "W",
  battery_charge_w: "W",
  battery_discharge_w: "W",
  battery_soc_pct: "%",
};

// wind_speed_kph and wind_direction_deg are deliberately absent: WeatherTiles
// renders them as one combined tile (speed + compass direction) below, not
// through this generic metric-to-label/unit lookup.
const WEATHER_TILE_LABELS: Record<string, string> = {
  temp_c: "Temperature",
  humidity_pct: "Humidity",
  rain_mm: "Rain",
  solar_radiation_wm2: "Solar radiation",
  barometric_pressure_hpa: "Barometric pressure",
};

const WEATHER_TILE_UNITS: Record<string, string> = {
  temp_c: "°C",
  humidity_pct: "%",
  rain_mm: "mm",
  solar_radiation_wm2: "W/m²",
  barometric_pressure_hpa: "hPa",
};

// Metrics shown rounded to one decimal place; anything not listed renders its raw value.
const ONE_DECIMAL_METRICS = new Set(["temp_c", "barometric_pressure_hpa"]);

// Power flow tiles are rounded to the nearest whole watt -- sub-watt precision isn't
// meaningful to a viewer glancing at the dashboard. battery_soc_pct is deliberately not
// in this set: it's a percentage, not a power flow, and isn't rounded.
const WHOLE_NUMBER_METRICS = new Set([
  "production_w",
  "consumption_w",
  "grid_import_w",
  "grid_export_w",
  "battery_charge_w",
  "battery_discharge_w",
]);

// Below this, production/battery flow readings are sensor noise, not real
// activity -- e.g. a few watts of inverter standby draw misreported as
// "production". Displayed (and colored/glowed) as a flat 0 W instead.
const ZERO_FLOOR_W = 100;
const ZERO_FLOOR_METRICS = new Set(["production_w", "battery_charge_w", "battery_discharge_w"]);

// The battery tile's background encodes charge level directly, red->amber->green,
// rather than the glow/accent system every other tile uses -- it's a continuous
// magnitude, not a flow direction. Routed through amber at the midpoint (not a
// straight red-to-green blend) since red-green color vision deficiency is common
// enough that a two-color gradient alone would be unreadable to some viewers; the
// numeric percentage is always shown too, so color is never the only cue.
const BATTERY_SOC_LOW_PCT = 10;

function formatMixPercent(value: number): string {
  return String(Math.round(value * 10) / 10);
}

/** CSS color-mix() between two theme tokens, `highVar` at `mixPct`% and the
 * remainder `lowVar` -- shared by both segments of the battery gradient
 * below, which differ only in which pair of tokens they blend. */
function cssColorMix(highVar: string, mixPct: number, lowVar: string): string {
  return `color-mix(in oklab, var(${highVar}) ${formatMixPercent(mixPct)}%, var(${lowVar}) ${formatMixPercent(100 - mixPct)}%)`;
}

function batterySocColor(pct: number): string {
  const clamped = Math.max(0, Math.min(100, pct));
  const t = Math.max(0, Math.min(1, (clamped - BATTERY_SOC_LOW_PCT) / (100 - BATTERY_SOC_LOW_PCT)));
  if (t <= 0.5) {
    return cssColorMix("--color-solar-soft", (t / 0.5) * 100, "--color-danger-soft");
  }
  return cssColorMix("--color-flow-soft", ((t - 0.5) / 0.5) * 100, "--color-solar-soft");
}

/** Same diagonal soft-gradient shape as .solar-glow/.flow-glow/.danger-glow
 * in index.css (linear-gradient(120deg, color, surface 45%, color 90%)),
 * just with a computed color instead of a fixed accent token -- so the
 * battery tile's background looks consistent with every other live tile
 * instead of a flat solid fill. */
function batterySocBackground(pct: number): string {
  const color = batterySocColor(pct);
  return `linear-gradient(120deg, ${color}, var(--color-surface) 45%, ${color} 90%)`;
}

function isZeroFloored(metric: string, value: number): boolean {
  return ZERO_FLOOR_METRICS.has(metric) && Math.abs(value) < ZERO_FLOOR_W;
}

function formatMetricValue(metric: string, value: number): string {
  if (isZeroFloored(metric, value)) {
    return "0";
  }
  if (ONE_DECIMAL_METRICS.has(metric)) {
    return value.toFixed(1);
  }
  if (WHOLE_NUMBER_METRICS.has(metric)) {
    return String(Math.round(value));
  }
  return String(value);
}

function latestByMetric(readings: Reading[]): Map<string, Reading> {
  const byMetric = new Map<string, Reading>();
  for (const reading of readings) {
    const existing = byMetric.get(reading.metric);
    if (!existing || reading.ts > existing.ts) {
      byMetric.set(reading.metric, reading);
    }
  }
  return byMetric;
}

// Each pair's two metrics are mutually exclusive within a single poll cycle
// (the collector emits only one, depending on the sign of that cycle's net
// flow), but a trailing live window can still catch one of each from
// different cycles -- e.g. when the flow is oscillating near zero.
const MUTUALLY_EXCLUSIVE_METRIC_PAIRS: [string, string][] = [
  ["grid_import_w", "grid_export_w"],
  ["battery_charge_w", "battery_discharge_w"],
];

/** Keep only whichever of each pair is actually newer, so the dashboard
 * never shows both sides of the same flow at once. */
function collapseMutuallyExclusiveFlows(latest: Map<string, Reading>): Map<string, Reading> {
  const collapsed = new Map(latest);
  for (const [a, b] of MUTUALLY_EXCLUSIVE_METRIC_PAIRS) {
    const readingA = collapsed.get(a);
    const readingB = collapsed.get(b);
    if (readingA && readingB) {
      collapsed.delete(readingA.ts >= readingB.ts ? b : a);
    }
  }
  return collapsed;
}

// Color encodes the real direction of energy flow, not brand decoration:
// amber = the sun producing, teal = power flowing to your benefit (export/
// charge), blue = power you're drawing from the grid, red = discharging the
// battery or importing from the grid -- stored energy or money leaving the
// site, the same red/green convention as the history chart's grid-flow bars.
const METRIC_ACCENT: Record<string, "solar" | "flow" | "grid" | "danger"> = {
  production_w: "solar",
  grid_export_w: "flow",
  battery_charge_w: "flow",
  grid_import_w: "danger",
  battery_discharge_w: "danger",
};

// Only these tiles get the animated gradient background, and only while their
// value is real activity (not zero-floored) -- it's a "something is actively
// happening" indicator, not decoration every tile should have. Grid import/
// export aren't zero-floored (see ZERO_FLOOR_METRICS), so their glow is simply
// on whenever that tile is showing at all.
const TILE_GLOW: Record<string, "solar" | "flow" | "danger"> = {
  production_w: "solar",
  battery_charge_w: "flow",
  battery_discharge_w: "danger",
  grid_import_w: "danger",
  grid_export_w: "flow",
};

function MetricTileGrid({
  readings,
  labels,
  units,
  siteId,
}: {
  readings: Reading[];
  labels: Record<string, string>;
  units: Record<string, string>;
  siteId: number;
}) {
  const latest = collapseMutuallyExclusiveFlows(latestByMetric(readings));
  const entries = Array.from(latest.entries());
  const hero = entries.find(([metric]) => metric === "production_w");
  const rest = entries.filter(([metric]) => metric !== "production_w");
  const heroZeroed = hero ? isZeroFloored(hero[0], hero[1].value) : false;

  const restTiles = rest.map(([metric, reading]) => {
    const zeroed = isZeroFloored(metric, reading.value);
    return (
      <Tile
        key={metric}
        label={labels[metric] ?? metric}
        value={`${formatMetricValue(metric, reading.value)} ${units[metric] ?? ""}`}
        accent={zeroed ? undefined : METRIC_ACCENT[metric]}
        glow={zeroed ? undefined : TILE_GLOW[metric]}
        backgroundImage={metric === "battery_soc_pct" ? batterySocBackground(reading.value) : undefined}
      />
    );
  });

  // Inserted right after the battery charge/discharge tile (whichever of the
  // pair is currently showing), not appended: today's running cost always,
  // then -- only once a grid direction is known -- the matching buy/sell
  // price right after it.
  const batteryIndex = rest.findIndex(([metric]) => metric === "battery_charge_w" || metric === "battery_discharge_w");
  const insertAt = batteryIndex === -1 ? restTiles.length : batteryIndex + 1;
  const direction = latest.has("grid_import_w") ? "import" : latest.has("grid_export_w") ? "export" : undefined;
  restTiles.splice(
    insertAt,
    0,
    <TodayCostTile key="today-cost" siteId={siteId} />,
    ...(direction ? [<GridPriceTile key="grid-price" siteId={siteId} direction={direction} />] : []),
  );

  return (
    <div className="space-y-4">
      {hero && (
        <Tile
          label={labels[hero[0]] ?? hero[0]}
          value={`${formatMetricValue(hero[0], hero[1].value)} ${units[hero[0]] ?? ""}`}
          accent={heroZeroed ? undefined : "solar"}
          glow={heroZeroed ? undefined : TILE_GLOW[hero[0]]}
          hero
        />
      )}
      {restTiles.length > 0 && <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">{restTiles}</div>}
    </div>
  );
}

function windTileValue(speed: Reading, direction: Reading | undefined): string {
  const formattedSpeed = `${speed.value.toFixed(1)} km/h`;
  return direction ? `${formattedSpeed} ${compassDirection(direction.value)}` : formattedSpeed;
}

/**
 * Weather tiles, separate from the generic MetricTileGrid: temperature and
 * barometric pressure round to one decimal place, and wind speed/direction
 * merge into a single tile (e.g. "12.3 km/h NE") instead of two.
 */
function WeatherTiles({ readings }: { readings: Reading[] }) {
  const latest = latestByMetric(readings);
  const windSpeed = latest.get("wind_speed_kph");
  const windDirection = latest.get("wind_direction_deg");
  const otherMetrics = Array.from(latest.entries()).filter(
    ([metric]) => metric !== "wind_speed_kph" && metric !== "wind_direction_deg",
  );

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {windSpeed && <Tile label="Wind speed" value={windTileValue(windSpeed, windDirection)} />}
      {otherMetrics.map(([metric, reading]) => (
        <Tile
          key={metric}
          label={WEATHER_TILE_LABELS[metric] ?? metric}
          value={`${formatMetricValue(metric, reading.value)} ${WEATHER_TILE_UNITS[metric] ?? ""}`}
        />
      ))}
    </div>
  );
}

function LiveTiles({ siteId }: { siteId: number }) {
  const energy = useLatestEnergyReadings(siteId);
  const energyReadings = averageLiveReadings(energy.data, Date.now());
  const weather = useLatestWeatherReadings(siteId);

  return (
    <div className="space-y-6" data-testid="live-tiles">
      {energy.isError && <ErrorAlert message="Failed to load live readings." />}
      <MetricTileGrid readings={energyReadings} labels={TILE_LABELS} units={TILE_UNITS} siteId={siteId} />
      <BillingPeriodTiles siteId={siteId} />
      <div>
        <p className="mb-3 text-sm font-medium text-ink-muted">Weather</p>
        {weather.isError && <ErrorAlert message="Failed to load live weather." />}
        <WeatherTiles readings={weather.data} />
      </div>
    </div>
  );
}

/**
 * Today's running cost so far, independent of the history chart's own date
 * range/method controls below -- this is a live tile (always "today, net"),
 * not a view into whatever period the chart happens to be showing.
 * Formatted the same way as the chart tooltip's cost row: sign dropped, red
 * for a net cost / green for a net credit, since color already conveys it.
 */
function TodayCostTile({ siteId }: { siteId: number }) {
  const { data: cost, isError } = useTodayCost(siteId, "net");

  if (isError) {
    return <ErrorAlert message="Failed to load today's cost." />;
  }
  if (!cost) {
    return null;
  }
  const dollars = cost.total_cost_c / 100;
  const [value] = formatTooltipValue(dollars, "Cost");
  const accent = signAccent(dollars);
  const label = dollars < 0 ? "Today's credit" : "Today's cost";
  return <Tile label={label} value={value} accent={accent} glow={accent} />;
}

/**
 * The running cost/credit for the current billing period so far, from its
 * start up to now -- the billing-cycle analogue of TodayCostTile above, same
 * formatting and sign convention. Full-width rather than part of the power
 * tile grid, since it summarizes a much longer span than a live reading.
 * When a monthly fee discount covers any of this period, an "Including
 * Discounts" tile showing the gross discount total (not net of the normal
 * fee) sits beside it at the regular tile size and the Billing Period tile takes the other two
 * tile spaces. A 404 (no billing cycle configured yet)
 * is a normal state, not an error -- nothing renders.
 */
function BillingPeriodTiles({ siteId }: { siteId: number }) {
  const { data: cost, isError, error } = useBillingPeriodCost(siteId, "net");

  if (isError) {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    return <ErrorAlert message="Failed to load the billing period cost." />;
  }
  if (!cost) {
    return null;
  }
  const dollars = cost.total_cost_c / 100;
  const [value] = formatTooltipValue(dollars, "Cost");
  const accent = signAccent(dollars);
  const discountDollars = cost.monthly_fee_discounts_c / 100;
  const hasDiscounts = discountDollars > 0;
  const [discountValue] = formatTooltipValue(discountDollars, "Discounts");
  return (
    <div className={hasDiscounts ? "grid gap-4 sm:grid-cols-3" : undefined}>
      <div className="sm:col-span-2">
        <Tile label="Billing Period" value={value} accent={accent} glow={accent} />
      </div>
      {hasDiscounts && <Tile label="Including Discounts" value={discountValue} accent="flow" glow="flow" />}
    </div>
  );
}

/**
 * Shows whichever price currently applies -- buy while importing, sell while
 * exporting -- as a plain positive rate: direction is conveyed by color (red
 * import / green export) and the glow, the same convention as the grid
 * import/export tiles, so there's no need to show Amber's raw feed-in sign
 * (negative in the normal case -- the customer is paid to export; positive
 * only during a rare negative-FiT event, where it would read as a cost here).
 */
function GridPriceTile({ siteId, direction }: { siteId: number; direction: "import" | "export" }) {
  const channel: PriceChannel = direction === "import" ? "buy" : "sell";
  const { data: price, isError } = useCurrentPrice(siteId, channel);

  if (isError) {
    return <ErrorAlert message={`Failed to load the grid ${direction} price.`} />;
  }
  if (!price) {
    return null;
  }
  const accent = direction === "import" ? "danger" : "flow";
  const label = direction === "import" ? "Grid import price" : "Grid export price";
  return <Tile label={label} value={`${priceMagnitude(price.value).toFixed(1)}c/kWh`} accent={accent} glow={accent} />;
}

/**
 * Grid flow and cost for today only, with no controls or totals -- always
 * today at 5-minute intervals, independent of the history card's range. It
 * follows the shared zoom with the history chart.
 */
function TodayChart({ siteId, zoom }: { siteId: number; zoom: ChartZoom }) {
  const day = useMemo(() => todayRange(), []);
  const range = useMemo(() => clipToDay(zoom.range, day), [zoom.range, day]);
  const data = useGridCostSeries(siteId, range, "net");

  return (
    <div className="rounded-xl border border-line bg-surface p-5" data-testid="today-chart">
      {(data.readingsFailed || data.costSeriesFailed) && <ErrorAlert message="Failed to load today's chart." />}
      {zoom.range && (
        <div className="mb-2 flex justify-end">
          <ResetZoomButton onClick={() => zoom.set(null)} />
        </div>
      )}
      <HistoryChart
        importReadings={data.importReadings}
        exportReadings={data.exportReadings}
        costSeries={data.costSeries}
        range={range}
        bucket={data.bucket}
        onZoom={(fromTs, toTs) => zoom.set(zoomRangeFor(fromTs, toTs, data.bucket))}
        syncId={TODAY_CHARTS_SYNC_ID}
      />
    </div>
  );
}

/**
 * " - DEMAND WINDOW ACTIVE", shown only while a DemandWindow currently
 * matches -- a sibling of the heading rather than text inside it, so the
 * heading's own accessible name stays plain "Dashboard" regardless of
 * whether a window is active. A 404 (no window active) or any other
 * failure both just mean nothing to show here.
 */
function DemandWindowNotice({ siteId }: { siteId: number }) {
  const { data } = useActiveDemandWindow(siteId);
  if (!data) {
    return null;
  }
  return <span className="font-display text-2xl font-semibold text-danger"> - DEMAND WINDOW ACTIVE</span>;
}

export function DashboardPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const parsedSiteId = Number(siteId);
  const [zoomRange, setZoomRange] = useState<DateRange | null>(null);
  const zoom = useMemo(() => ({ range: zoomRange, set: setZoomRange }), [zoomRange]);

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <div>
        <div className="flex flex-wrap items-baseline">
          <h1 className="font-display text-2xl font-semibold text-ink">Dashboard</h1>
          <DemandWindowNotice siteId={parsedSiteId} />
        </div>
        <DataFreshness siteId={parsedSiteId} />
      </div>
      <LiveTiles siteId={parsedSiteId} />
      <TodayPriceChart siteId={parsedSiteId} zoom={zoom} />
      <TodayChart siteId={parsedSiteId} zoom={zoom} />
    </div>
  );
}
