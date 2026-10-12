import { useMemo, useState } from "react";
import DatePicker from "react-datepicker";
import "react-datepicker/dist/react-datepicker.css";
import { useParams } from "react-router-dom";
import { Bar, Cell, ComposedChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError } from "../api/client";
import { compassDirection } from "../dashboard/compass";
import {
  buildAxisTickPlan,
  buildHistoryChartData,
  formatBucketTick,
  formatTooltipValue,
  type HistoryChartPoint,
} from "../dashboard/historyChart";
import {
  buildPriceChartData,
  mergeImportExportPrices,
  priceMagnitude,
  type TodayPricingPoint,
} from "../dashboard/priceChart";
import {
  type CostBucket,
  type CostSeriesPoint,
  type DateRange,
  useBillingPeriodCost,
  useCost,
  useCostSeries,
  useHistoricalEnergyReadings,
  useTodayCost,
  useTodayPrices,
} from "../dashboard/useHistoricalData";
import {
  type PriceChannel,
  type Reading,
  useCurrentPrice,
  useLatestEnergyReadings,
  useLatestWeatherReadings,
} from "../dashboard/useLiveReadings";

/** The chart's bar metrics: actual grid flow, regardless of the net/gross cost method. */
const CHART_IMPORT_METRIC = "grid_import_w";
const CHART_EXPORT_METRIC = "grid_export_w";

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

const ACCENT_TEXT: Record<string, string> = {
  solar: "text-solar",
  flow: "text-flow",
  grid: "text-grid",
  danger: "text-danger",
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

const GLOW_CLASS: Record<"solar" | "flow" | "danger", string> = {
  solar: "solar-glow",
  flow: "flow-glow",
  danger: "danger-glow",
};

function ErrorAlert({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
      {message}
    </p>
  );
}

function Tile({
  label,
  value,
  accent,
  glow,
  hero = false,
  backgroundImage,
}: {
  label: string;
  value: string;
  accent?: "solar" | "flow" | "grid" | "danger";
  glow?: "solar" | "flow" | "danger";
  hero?: boolean;
  backgroundImage?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-line bg-surface ${glow ? GLOW_CLASS[glow] : ""} ${hero ? "p-6" : "p-4"}`}
      style={backgroundImage ? { backgroundImage } : undefined}
    >
      <p className="text-sm text-ink-muted">{label}</p>
      <p
        className={`mt-1 font-display tabular-nums font-semibold ${hero ? "text-4xl" : "text-2xl"} ${
          accent ? ACCENT_TEXT[accent] : "text-ink"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

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
  const weather = useLatestWeatherReadings(siteId);

  return (
    <div className="space-y-6" data-testid="live-tiles">
      {energy.isError && <ErrorAlert message="Failed to load live readings." />}
      <MetricTileGrid readings={energy.data} labels={TILE_LABELS} units={TILE_UNITS} siteId={siteId} />
      <BillingPeriodCostTile siteId={siteId} />
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
 * tile grid, since it summarizes a much longer span than a live reading. A
 * 404 (no billing cycle configured yet) is a normal state, not an error --
 * the tile just doesn't render.
 */
function BillingPeriodCostTile({ siteId }: { siteId: number }) {
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
  const label = dollars < 0 ? "Billing period credit" : "Billing period cost";
  return <Tile label={label} value={value} accent={accent} glow={accent} />;
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

type RangeMode = "today" | "yesterday" | "7d" | "30d" | "custom";

const RANGE_MODE_LABELS: Record<RangeMode, string> = {
  today: "Today",
  yesterday: "Yesterday",
  "7d": "7 days",
  "30d": "30 days",
  custom: "Custom range",
};

function startOfDay(date: Date): Date {
  const start = new Date(date);
  start.setHours(0, 0, 0, 0);
  return start;
}

function addDays(date: Date, days: number): Date {
  return new Date(date.getTime() + days * 24 * 60 * 60_000);
}

/** Today/7 days/30 days all start at local midnight and run through the end of
 * today (even the part that hasn't happened yet) rather than a rolling N*24h
 * window, so a day's bars/line always cover the same fixed calendar span no
 * matter what time "now" is -- the not-yet-happened part reads as zero.
 * Yesterday is the one fully-elapsed exception: it stops at today's midnight
 * rather than running into the future. */
function rangeForMode(mode: RangeMode, custom: DateRange): DateRange {
  if (mode === "custom") {
    return custom;
  }
  if (mode === "yesterday") {
    const from = addDays(startOfDay(new Date()), -1);
    return { from, to: addDays(from, 1) };
  }
  const daysBack = mode === "today" ? 0 : mode === "7d" ? 6 : 29;
  const to = addDays(startOfDay(new Date()), 1);
  const from = addDays(to, -1 - daysBack);
  return { from, to };
}

const SEVEN_DAYS_MS = 7 * 24 * 60 * 60_000;
const THIRTY_DAYS_MS = 30 * 24 * 60 * 60_000;

/** Finer resolution for shorter spans, coarser for longer ones, so the chart
 * stays both detailed and readable at every zoom level. */
function bucketForRange(range: DateRange): CostBucket {
  const durationMs = range.to.getTime() - range.from.getTime();
  if (durationMs < SEVEN_DAYS_MS) {
    return "15min";
  }
  if (durationMs < THIRTY_DAYS_MS) {
    return "hour";
  }
  return "6hour";
}

function MethodRadio({
  method,
  selected,
  onSelect,
}: {
  method: "net" | "gross";
  selected: boolean;
  onSelect: () => void;
}) {
  const label = method === "net" ? "Net" : "Gross";
  return (
    <label className="flex items-center gap-1 text-sm text-ink-muted">
      <input type="radio" name="method" checked={selected} onChange={onSelect} aria-label={label} />
      {label}
    </label>
  );
}

/** The dashboard's one sign convention, used everywhere a number means "cost
 * vs. credit" or "import vs. export": positive -> danger (red), negative ->
 * flow (green), exactly zero -> neutral (each call site picks its own neutral
 * color/class, since "zero" renders differently as a Tile accent vs. a raw
 * chart color). */
function signAccent(value: number): "danger" | "flow" | undefined {
  if (value > 0) {
    return "danger";
  }
  if (value < 0) {
    return "flow";
  }
  return undefined;
}

const ACCENT_CSS_VAR: Record<"solar" | "flow" | "grid" | "danger", string> = {
  solar: "var(--color-solar)",
  flow: "var(--color-flow)",
  grid: "var(--color-grid)",
  danger: "var(--color-danger)",
};

/** Red while importing (costing money), green while exporting (earning money) --
 * the same convention as the "Grid import"/"Grid export" tiles' text color.
 * Exactly zero (e.g. a not-yet-happened bucket) is neutral, not red, since
 * red would otherwise misread as "importing". */
function gridBarColor(value: number): string {
  const accent = signAccent(value);
  return accent ? ACCENT_CSS_VAR[accent] : "var(--color-line)";
}

/** Same red/green convention as gridBarColor, as a raw CSS color for inline
 * styling (the chart tooltip's cost row). */
function costTextColor(value: number): string {
  const accent = signAccent(value);
  return accent ? ACCENT_CSS_VAR[accent] : "var(--color-ink-muted)";
}

// Fixed legend/tooltip swatch colors, independent of any series' recharts
// `fill`/`stroke` -- the "Grid" bar has no single fill of its own (each bar's
// color comes from a per-point Cell instead, since it swings between import
// and export), so relying on recharts' own item color would leave the
// tooltip/legend with no color of their own to fall back on.
const SWATCH_COLORS = {
  gridImport: "var(--color-danger)",
  gridExport: "var(--color-flow)",
  cost: "var(--color-solar)",
} as const;

function LegendSwatch({
  color,
  label,
  textColor,
}: {
  color: string;
  label: string;
  /** Inline text color, e.g. for a live reading whose sign the text itself
   * should convey (red/green). Defaults to the neutral ink-muted token --
   * the static legend and the Grid tooltip row keep that default. */
  textColor?: string;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span aria-hidden className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
      <span className={textColor ? undefined : "text-ink-muted"} style={textColor ? { color: textColor } : undefined}>
        {label}
      </span>
    </span>
  );
}

/** Built in plain HTML/Tailwind rather than recharts' own <Legend>, so its text
 * always renders in the theme's ink tokens -- never a series color -- and reads
 * correctly in both light and dark mode. */
function HistoryChartLegend() {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-4 text-xs">
      <LegendSwatch color={SWATCH_COLORS.gridImport} label="Grid import" />
      <LegendSwatch color={SWATCH_COLORS.gridExport} label="Grid export" />
      <LegendSwatch color={SWATCH_COLORS.cost} label="Cost" />
    </div>
  );
}

/** Custom tooltip content, not recharts' default: its text always renders in
 * the theme's ink tokens, same reasoning as HistoryChartLegend above -- the
 * default content colors each row from the series' own fill/stroke, which is
 * unset for "Grid" and otherwise unreadable against a dark surface. */
function HistoryChartTooltip({
  active,
  label,
  payload,
  bucket,
}: {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ payload?: HistoryChartPoint }>;
  bucket: CostBucket;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point || label === undefined) {
    return null;
  }
  const [gridValue] = formatTooltipValue(point.gridKwh, "Grid");
  const [costValue] = formatTooltipValue(point.costDollars, "Cost");
  const costColor = costTextColor(point.costDollars);
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-lg">
      <p className="mb-1 text-ink-muted">{formatBucketTick(Number(label), bucket)}</p>
      <LegendSwatch color={gridBarColor(point.gridKwh)} label={gridValue} />
      <LegendSwatch color={costColor} textColor={costColor} label={costValue} />
    </div>
  );
}

function HistoryChart({
  importReadings,
  exportReadings,
  costSeries,
  range,
  bucket,
}: {
  importReadings: Reading[];
  exportReadings: Reading[];
  costSeries: CostSeriesPoint[];
  range: DateRange;
  bucket: CostBucket;
}) {
  const chartData = useMemo(
    () => buildHistoryChartData(importReadings, exportReadings, costSeries, range, bucket),
    [importReadings, exportReadings, costSeries, range, bucket],
  );
  const axisTickPlan = useMemo(() => buildAxisTickPlan(chartData, range), [chartData, range]);
  return (
    <>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={chartData}>
          <XAxis
            dataKey="ts"
            ticks={axisTickPlan.ticks}
            tickFormatter={(ts: number) => axisTickPlan.formatTick(Number(ts))}
            stroke="var(--color-ink-muted)"
            fontSize={12}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            yAxisId="grid"
            stroke="var(--color-ink-muted)"
            fontSize={12}
            tickLine={false}
            axisLine={false}
            width={48}
            label={{
              value: "kWh",
              angle: -90,
              position: "insideLeft",
              style: { fill: "var(--color-ink-muted)", fontSize: 12, textAnchor: "middle" },
            }}
          />
          <YAxis
            yAxisId="cost"
            orientation="right"
            stroke="var(--color-ink-muted)"
            fontSize={12}
            tickLine={false}
            axisLine={false}
            width={48}
            label={{
              value: "$",
              angle: -90,
              position: "insideRight",
              style: { fill: "var(--color-ink-muted)", fontSize: 12, textAnchor: "middle" },
            }}
          />
          <Tooltip content={(props) => <HistoryChartTooltip {...props} bucket={bucket} />} />
          <Bar yAxisId="grid" dataKey="gridKwh" name="Grid" radius={2}>
            {chartData.map((point) => (
              <Cell key={point.ts} fill={gridBarColor(point.gridKwh)} />
            ))}
          </Bar>
          <Line
            yAxisId="cost"
            type="monotone"
            dataKey="costDollars"
            name="Cost"
            stroke="var(--color-solar)"
            strokeWidth={2}
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <HistoryChartLegend />
    </>
  );
}

function TodayPricingTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ payload?: TodayPricingPoint }>;
}) {
  const point = payload?.[0]?.payload;
  const importValue = point?.actualImportCentsPerKwh ?? point?.predictedImportCentsPerKwh;
  const exportValue = point?.actualExportCentsPerKwh ?? point?.predictedExportCentsPerKwh;
  if (!active || !point || label === undefined || (importValue == null && exportValue == null)) {
    return null;
  }
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-lg">
      <p className="mb-1 text-ink-muted">{formatBucketTick(Number(label), "15min")}</p>
      {importValue != null && (
        <p style={{ color: SWATCH_COLORS.gridImport }}>
          Import {importValue.toFixed(1)}c/kWh{point.actualImportCentsPerKwh === null && " (predicted)"}
        </p>
      )}
      {exportValue != null && (
        <p style={{ color: SWATCH_COLORS.gridExport }}>
          Export {exportValue.toFixed(1)}c/kWh{point.actualExportCentsPerKwh === null && " (predicted)"}
        </p>
      )}
    </div>
  );
}

function PriceLegendSwatch({ color, dashed, label }: { color: string; dashed?: boolean; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="h-0.5 w-4"
        style={
          dashed
            ? { backgroundImage: `repeating-linear-gradient(to right, ${color} 0 4px, transparent 4px 7px)` }
            : { backgroundColor: color }
        }
      />
      {label}
    </span>
  );
}

/**
 * Amber's actual (elapsed) and predicted (forecast) import and export prices
 * for today, at 5-minute resolution -- always today, independent of the
 * range/method controls below (same "live tile, not a view of the selected
 * range" reasoning as TodayCostTile). Solid where actual, dashed where
 * predicted; import and export each get their own color (red/green, matching
 * the grid tiles elsewhere on this page) since they're two separate series.
 */
function todayRange(): DateRange {
  const from = startOfDay(new Date());
  return { from, to: addDays(from, 1) };
}

function buildTodayPricingChartData(importPrices: Reading[], exportPrices: Reading[]): TodayPricingPoint[] {
  const now = new Date();
  return mergeImportExportPrices(buildPriceChartData(importPrices, now), buildPriceChartData(exportPrices, now));
}

function TodayPriceChart({ siteId }: { siteId: number }) {
  const range = useMemo(() => todayRange(), []);
  const { data: importPrices, isError: isImportError } = useTodayPrices(siteId, "buy");
  const { data: exportPrices, isError: isExportError } = useTodayPrices(siteId, "sell");
  const chartData = useMemo(
    () => buildTodayPricingChartData(importPrices ?? [], exportPrices ?? []),
    [importPrices, exportPrices],
  );
  const axisTickPlan = useMemo(() => buildAxisTickPlan(chartData, range), [chartData, range]);

  return (
    <div className="rounded-xl border border-line bg-surface p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-lg font-semibold text-ink">Today's Pricing</h2>
        <div className="flex flex-wrap items-center gap-4 text-xs text-ink-muted">
          <PriceLegendSwatch color={SWATCH_COLORS.gridImport} label="Import actual" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridImport} dashed label="Import predicted" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridExport} label="Export actual" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridExport} dashed label="Export predicted" />
        </div>
      </div>
      {(isImportError || isExportError) && <ErrorAlert message="Failed to load today's pricing." />}
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData}>
          <XAxis
            dataKey="ts"
            ticks={axisTickPlan.ticks}
            tickFormatter={(ts: number) => axisTickPlan.formatTick(Number(ts))}
            stroke="var(--color-ink-muted)"
            fontSize={12}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            stroke="var(--color-ink-muted)"
            fontSize={12}
            tickLine={false}
            axisLine={false}
            width={48}
            label={{
              value: "c/kWh",
              angle: -90,
              position: "insideLeft",
              style: { fill: "var(--color-ink-muted)", fontSize: 12, textAnchor: "middle" },
            }}
          />
          <Tooltip content={(props) => <TodayPricingTooltip {...props} />} />
          {/* Piecewise-constant per 5-minute interval, not a smooth trend -- a
              step line matches how the price actually moves. */}
          <Line
            type="stepAfter"
            dataKey="actualImportCentsPerKwh"
            stroke={SWATCH_COLORS.gridImport}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="stepAfter"
            dataKey="predictedImportCentsPerKwh"
            stroke={SWATCH_COLORS.gridImport}
            strokeDasharray="4 3"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="stepAfter"
            dataKey="actualExportCentsPerKwh"
            stroke={SWATCH_COLORS.gridExport}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="stepAfter"
            dataKey="predictedExportCentsPerKwh"
            stroke={SWATCH_COLORS.gridExport}
            strokeDasharray="4 3"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const DATE_PICKER_INPUT_CLASS =
  "rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";

function HistoryAndCost({ siteId }: { siteId: number }) {
  const [rangeMode, setRangeMode] = useState<RangeMode>("today");
  const [customRange, setCustomRange] = useState<DateRange>(() => rangeForMode("today", { from: new Date(), to: new Date() }));
  const [method, setMethod] = useState<"net" | "gross">("net");
  const range = useMemo(() => rangeForMode(rangeMode, customRange), [rangeMode, customRange]);
  const bucket = bucketForRange(range);
  const { data: importHistory, isError: importHistoryFailed } = useHistoricalEnergyReadings(
    siteId,
    range,
    CHART_IMPORT_METRIC,
  );
  const { data: exportHistory, isError: exportHistoryFailed } = useHistoricalEnergyReadings(
    siteId,
    range,
    CHART_EXPORT_METRIC,
  );
  const { data: cost, isError: costFailed } = useCost(siteId, range, method);
  const { data: costSeries, isError: costSeriesFailed } = useCostSeries(siteId, range, method, bucket);
  const costTileAccent = cost ? signAccent(cost.total_cost_c) : undefined;

  return (
    <div className="rounded-xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center gap-4">
        <select
          value={rangeMode}
          onChange={(e) => setRangeMode(e.target.value as RangeMode)}
          aria-label="Date range"
          className={DATE_PICKER_INPUT_CLASS}
        >
          {(Object.keys(RANGE_MODE_LABELS) as RangeMode[]).map((mode) => (
            <option key={mode} value={mode}>
              {RANGE_MODE_LABELS[mode]}
            </option>
          ))}
        </select>
        {rangeMode === "custom" && (
          <>
            <DatePicker
              selected={customRange.from}
              onChange={(date: Date | null) => date && setCustomRange((prev) => ({ ...prev, from: date }))}
              showTimeSelect
              timeIntervals={15}
              dateFormat="yyyy-MM-dd HH:mm"
              aria-label="Custom range start"
              className={DATE_PICKER_INPUT_CLASS}
            />
            <span className="text-sm text-ink-muted">to</span>
            <DatePicker
              selected={customRange.to}
              onChange={(date: Date | null) => date && setCustomRange((prev) => ({ ...prev, to: date }))}
              showTimeSelect
              timeIntervals={15}
              dateFormat="yyyy-MM-dd HH:mm"
              aria-label="Custom range end"
              className={DATE_PICKER_INPUT_CLASS}
            />
          </>
        )}
        <MethodRadio method="net" selected={method === "net"} onSelect={() => setMethod("net")} />
        <MethodRadio method="gross" selected={method === "gross"} onSelect={() => setMethod("gross")} />
      </div>
      <div className="mt-4 space-y-1" data-testid="history-cost">
        {costFailed && <ErrorAlert message="Failed to load cost." />}
        {(importHistoryFailed || exportHistoryFailed) && (
          <ErrorAlert message="Failed to load historical readings." />
        )}
        {costSeriesFailed && <ErrorAlert message="Failed to load the cost trend." />}
        {cost && (
          <Tile
            label="Cost"
            value={`$${(cost.total_cost_c / 100).toFixed(2)}`}
            accent={costTileAccent}
            glow={costTileAccent}
          />
        )}
      </div>
      <div className="mt-4">
        <HistoryChart
          importReadings={importHistory ?? []}
          exportReadings={exportHistory ?? []}
          costSeries={costSeries ?? []}
          range={range}
          bucket={bucket}
        />
      </div>
    </div>
  );
}

export function DashboardPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const parsedSiteId = Number(siteId);

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <h1 className="font-display text-2xl font-semibold text-ink">Dashboard</h1>
      <LiveTiles siteId={parsedSiteId} />
      <TodayPriceChart siteId={parsedSiteId} />
      <HistoryAndCost siteId={parsedSiteId} />
    </div>
  );
}
