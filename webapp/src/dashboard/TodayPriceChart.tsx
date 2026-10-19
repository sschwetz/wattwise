import { useMemo } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorAlert, ResetZoomButton, ZoomSelectionArea } from "./chartParts";
import { SWATCH_COLORS, ZOOMABLE_CHART_STYLE } from "./chartStyle";
import { clipToDay, todayRange, zoomRangeFor } from "./dateRange";
import { buildAxisTickPlan, formatBucketTick } from "./historyChart";
import { buildPriceChartData, mergeImportExportPrices, type TodayPricingPoint } from "./priceChart";
import { type ChartZoom, useChartZoom } from "./useChartZoom";
import { useTodayPrices } from "./useHistoricalData";
import type { Reading } from "./useLiveReadings";

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
      <p className="mb-1 text-ink-muted">{formatBucketTick(Number(label), "5min")}</p>
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

function buildTodayPricingChartData(importPrices: Reading[], exportPrices: Reading[]): TodayPricingPoint[] {
  const now = new Date();
  return mergeImportExportPrices(buildPriceChartData(importPrices, now), buildPriceChartData(exportPrices, now));
}

/**
 * Amber's actual (elapsed) and predicted (forecast) import and export prices
 * for today, at 5-minute resolution. Solid where actual, dashed where
 * predicted; import and export each get their own color (red/green, matching
 * the grid tiles on the dashboard) since they're two separate series.
 * Drag across the chart to zoom; the zoom is shared with the page's other
 * today chart.
 */
export function TodayPriceChart({ siteId, zoom }: { siteId: number; zoom: ChartZoom }) {
  const dayRange = useMemo(() => todayRange(), []);
  const range = useMemo(() => clipToDay(zoom.range, dayRange), [zoom.range, dayRange]);
  const { handlers, selection } = useChartZoom((fromTs, toTs) => zoom.set(zoomRangeFor(fromTs, toTs, "5min")));
  const { data: importPrices, isError: isImportError } = useTodayPrices(siteId, "buy");
  const { data: exportPrices, isError: isExportError } = useTodayPrices(siteId, "sell");
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
  const axisTickPlan = useMemo(() => buildAxisTickPlan(chartData, range), [chartData, range]);

  return (
    <div className="rounded-xl border border-line bg-surface p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-lg font-semibold text-ink">Today's Pricing</h2>
        <div className="flex flex-wrap items-center gap-4 text-xs text-ink-muted">
          {zoom.range && <ResetZoomButton onClick={() => zoom.set(null)} />}
          <PriceLegendSwatch color={SWATCH_COLORS.gridImport} label="Import actual" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridImport} dashed label="Import predicted" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridExport} label="Export actual" />
          <PriceLegendSwatch color={SWATCH_COLORS.gridExport} dashed label="Export predicted" />
        </div>
      </div>
      {(isImportError || isExportError) && <ErrorAlert message="Failed to load today's pricing." />}
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData} style={ZOOMABLE_CHART_STYLE} {...handlers}>
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
          <ZoomSelectionArea selection={selection} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
