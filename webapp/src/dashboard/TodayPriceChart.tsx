import { useMemo, useState } from "react";
import { Area, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorAlert, ResetZoomButton, ZoomSelectionArea } from "./chartParts";
import { SWATCH_COLORS, TODAY_CHARTS_SYNC_ID, ZOOMABLE_CHART_STYLE } from "./chartStyle";
import { clipToDay, dayRangeOffset, type PricingDayOffset, zoomRangeFor } from "./dateRange";
import { buildAxisTickPlan, formatBucketTick } from "./historyChart";
import { InstabilitySummary } from "./InstabilitySummary";
import {
  buildPriceChartData,
  findInstabilityWindows,
  mergeImportExportPrices,
  type PriceReading,
  type TodayPricingPoint,
  visibleInstabilitySpans,
} from "./priceChart";
import { type ChartZoom, useChartZoom } from "./useChartZoom";
import { type DateRange, useTodayPrices } from "./useHistoricalData";

/** Which series a tooltip value came from: actual shows nothing, a predicted
 * value is labelled, and a value with neither is the fallback series. */
function priceKindSuffix(actual: number | null, predicted: number | null): string {
  if (actual !== null) return "";
  return predicted !== null ? " (predicted)" : " (fallback: no advanced forecast)";
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
  const importValue =
    point?.actualImportCentsPerKwh ?? point?.predictedImportCentsPerKwh ?? point?.fallbackImportCentsPerKwh;
  const exportValue =
    point?.actualExportCentsPerKwh ?? point?.predictedExportCentsPerKwh ?? point?.fallbackExportCentsPerKwh;
  if (!active || !point || label === undefined || (importValue == null && exportValue == null)) {
    return null;
  }
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-lg">
      <p className="mb-1 text-ink-muted">{formatBucketTick(Number(label), "5min")}</p>
      {importValue != null && (
        <p style={{ color: SWATCH_COLORS.gridImport }}>
          Import {importValue.toFixed(1)}c/kWh
          {priceKindSuffix(point.actualImportCentsPerKwh, point.predictedImportCentsPerKwh)}
        </p>
      )}
      {point.importBand && (
        <p className="text-ink-muted">
          Range {point.importBand[0].toFixed(1)} to {point.importBand[1].toFixed(1)}c/kWh
        </p>
      )}
      {point.importSpikeStatus !== "none" && (
        <p className="text-ink-muted">
          {point.importSpikeStatus === "spike" ? "Price spike" : "Price spike possible"}
        </p>
      )}
      {exportValue != null && (
        <p style={{ color: SWATCH_COLORS.gridExport }}>
          Export {exportValue.toFixed(1)}c/kWh
          {priceKindSuffix(point.actualExportCentsPerKwh, point.predictedExportCentsPerKwh)}
        </p>
      )}
    </div>
  );
}

const SWATCH_PATTERNS = {
  dashed: (color: string) => `repeating-linear-gradient(to right, ${color} 0 4px, transparent 4px 7px)`,
  dotted: (color: string) => `repeating-linear-gradient(to right, ${color} 0 1px, transparent 1px 4px)`,
};

function PriceLegendSwatch({
  color,
  pattern,
  label,
}: {
  color: string;
  pattern?: keyof typeof SWATCH_PATTERNS;
  label: string;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="h-0.5 w-4"
        style={pattern ? { backgroundImage: SWATCH_PATTERNS[pattern](color) } : { backgroundColor: color }}
      />
      {label}
    </span>
  );
}

function RangeLegendSwatch() {
  return (
    <span className="flex items-center gap-1.5">
      <span aria-hidden className="h-2.5 w-4 rounded-sm opacity-30" style={{ backgroundColor: SWATCH_COLORS.gridImport }} />
      Forecast range
    </span>
  );
}

const SPIKE_LEVELS = {
  potential: { color: "var(--color-solar)", label: "Price spike possible" },
  spike: { color: "var(--color-danger)", label: "Price spike" },
} as const;

function SpikeLegendSwatch({ level }: { level: keyof typeof SPIKE_LEVELS }) {
  const { color, label } = SPIKE_LEVELS[level];
  return (
    <span className="flex items-center gap-1.5">
      <span aria-hidden className="h-2.5 w-4 rounded-sm opacity-30" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}

function buildTodayPricingChartData(importPrices: PriceReading[], exportPrices: PriceReading[]): TodayPricingPoint[] {
  const now = new Date();
  return mergeImportExportPrices(buildPriceChartData(importPrices, now), buildPriceChartData(exportPrices, now));
}

const DAY_TITLES: Record<PricingDayOffset, string> = { [-1]: "Yesterday's Pricing", 0: "Today's Pricing", 1: "Tomorrow's Pricing" };

/**
 * Amber's actual (elapsed) and predicted (forecast) import and export prices
 * for yesterday, today or tomorrow (`dayOffset`), at 5-minute resolution. Solid where actual, dashed where
 * predicted; import and export each get their own color (red/green, matching
 * the grid tiles on the dashboard) since they're two separate series.
 * Drag across the chart to zoom. With a `zoom` prop the zoom is shared with
 * the page's other today chart; without one the chart zooms on its own.
 */
export function TodayPriceChart({
  siteId,
  zoom: sharedZoom,
  dayOffset = 0,
}: {
  siteId: number;
  zoom?: ChartZoom;
  dayOffset?: PricingDayOffset;
}) {
  // Without a shared zoom the chart keeps its own, and it also stays out of
  // the page's hover/zoom sync group.
  const [ownRange, setOwnRange] = useState<DateRange | null>(null);
  const zoom = useMemo<ChartZoom>(() => sharedZoom ?? { range: ownRange, set: setOwnRange }, [sharedZoom, ownRange]);
  const title = DAY_TITLES[dayOffset];
  const dayRange = useMemo(() => dayRangeOffset(dayOffset), [dayOffset]);
  const range = useMemo(() => clipToDay(zoom.range, dayRange), [zoom.range, dayRange]);
  const { handlers, selection } = useChartZoom((fromTs, toTs) => zoom.set(zoomRangeFor(fromTs, toTs, "5min")));
  const { data: importPrices, isError: isImportError } = useTodayPrices(siteId, "buy", dayOffset);
  const { data: exportPrices, isError: isExportError } = useTodayPrices(siteId, "sell", dayOffset);
  const fullChartData = useMemo(
    () => buildTodayPricingChartData(importPrices ?? [], exportPrices ?? []),
    [importPrices, exportPrices],
  );
  const chartData = useMemo(
    () =>
      zoom.range
        ? fullChartData.filter((point) => point.ts >= range.from.getTime() && point.ts < range.to.getTime())
        : fullChartData,
    [fullChartData, zoom.range, range],
  );
  const hasFallback = useMemo(
    () => chartData.some((p) => p.fallbackImportCentsPerKwh !== null || p.fallbackExportCentsPerKwh !== null),
    [chartData],
  );
  const hasBand = useMemo(() => chartData.some((p) => p.importBand !== null), [chartData]);
  const axisTickPlan = useMemo(() => buildAxisTickPlan(chartData, range), [chartData, range]);
  // The summary covers the whole day; the tinted areas only what is on screen.
  const instabilityWindows = useMemo(() => findInstabilityWindows(fullChartData), [fullChartData]);
  const visibleSpans = useMemo(
    () =>
      visibleInstabilitySpans(
        instabilityWindows,
        chartData.map((p) => p.ts),
      ),
    [instabilityWindows, chartData],
  );

  return (
    <div className="rounded-xl border border-line bg-surface p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-lg font-semibold text-ink">{title}</h2>
        <div className="flex flex-wrap items-center gap-4 text-xs text-ink-muted">
          {zoom.range && <ResetZoomButton onClick={() => zoom.set(null)} />}
          <PriceLegendSwatch color={SWATCH_COLORS.gridImport} label="Import actual" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridImport} pattern="dashed" label="Import predicted" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridExport} label="Export actual" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridExport} pattern="dashed" label="Export predicted" />
          {hasBand && <RangeLegendSwatch />}
          {(["potential", "spike"] as const).map(
            (level) => visibleSpans.some((span) => span.level === level) && <SpikeLegendSwatch key={level} level={level} />,
          )}
          {hasFallback && (
            <PriceLegendSwatch color="var(--color-ink-muted)" pattern="dotted" label="Fallback (no advanced forecast)" />
          )}
        </div>
      </div>
      {(isImportError || isExportError) && <ErrorAlert message={`Failed to load ${title.toLowerCase()}.`} />}
      <InstabilitySummary windows={instabilityWindows} />
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={chartData} style={ZOOMABLE_CHART_STYLE} syncId={sharedZoom ? TODAY_CHARTS_SYNC_ID : undefined} syncMethod="value" {...handlers}>
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
          {visibleSpans.map((span) => (
            <ReferenceArea
              key={span.x1}
              x1={span.x1}
              x2={span.x2}
              fill={SPIKE_LEVELS[span.level].color}
              fillOpacity={0.15}
              stroke="none"
              ifOverflow="visible"
            />
          ))}
          {/* The forecast's low-to-high band, behind the lines. */}
          <Area
            type="stepAfter"
            dataKey="importBand"
            stroke="none"
            fill={SWATCH_COLORS.gridImport}
            fillOpacity={0.15}
            connectNulls={false}
            isAnimationActive={false}
            activeDot={false}
            legendType="none"
          />
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
          <Line
            type="stepAfter"
            dataKey="fallbackImportCentsPerKwh"
            stroke={SWATCH_COLORS.gridImport}
            strokeDasharray="1 3"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="stepAfter"
            dataKey="fallbackExportCentsPerKwh"
            stroke={SWATCH_COLORS.gridExport}
            strokeDasharray="1 3"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          <ZoomSelectionArea selection={selection} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
