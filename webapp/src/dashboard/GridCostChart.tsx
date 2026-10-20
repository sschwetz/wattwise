import { useMemo } from "react";
import { Bar, Cell, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ZoomSelectionArea } from "./chartParts";
import { SWATCH_COLORS, ZOOMABLE_CHART_STYLE } from "./chartStyle";
import {
  buildAxisTickPlan,
  buildCostAxis,
  buildHistoryChartData,
  formatBucketTick,
  formatTooltipValue,
  type HistoryChartPoint,
} from "./historyChart";
import { costTextColor, gridBarColor } from "./signColors";
import { useChartZoom } from "./useChartZoom";
import {
  type CostBucket,
  type CostSeriesPoint,
  type DateRange,
} from "./useHistoricalData";
import type { Reading } from "./useLiveReadings";

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
export function HistoryChartLegend() {
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

export function HistoryChart({
  importReadings,
  exportReadings,
  costSeries,
  range,
  bucket,
  onZoom,
  syncId,
}: {
  importReadings: Reading[];
  exportReadings: Reading[];
  costSeries: CostSeriesPoint[];
  range: DateRange;
  bucket: CostBucket;
  onZoom: (fromTs: number, toTs: number) => void;
  /** Charts sharing a syncId show their tooltip at the same timestamp. */
  syncId?: string;
}) {
  const { handlers, selection } = useChartZoom(onZoom);
  const chartData = useMemo(
    () => buildHistoryChartData(importReadings, exportReadings, costSeries, range, bucket),
    [importReadings, exportReadings, costSeries, range, bucket],
  );
  const costAxis = useMemo(() => buildCostAxis(chartData), [chartData]);
  const axisTickPlan = useMemo(() => buildAxisTickPlan(chartData, range), [chartData, range]);
  return (
    <>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={chartData} style={ZOOMABLE_CHART_STYLE} syncId={syncId} syncMethod="value" {...handlers}>
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
            ticks={costAxis.ticks}
            domain={costAxis.domain}
            tickFormatter={(v: number) => v.toFixed(2)}
            allowDataOverflow
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
            type="linear"
            dataKey="costDollars"
            name="Cost"
            stroke="var(--color-solar)"
            strokeWidth={2}
            dot={false}
          />
          <ZoomSelectionArea selection={selection} yAxisId="grid" />
        </ComposedChart>
      </ResponsiveContainer>
      <HistoryChartLegend />
    </>
  );
}
