import { useMemo } from "react";
import { Bar, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { BatteryChartPoint } from "./batteryFlows";
import { LegendSwatch, ZoomSelectionArea } from "./chartParts";
import { axisProps, SWATCH_COLORS, useChartColors, yAxisProps, ZOOMABLE_CHART_STYLE } from "./chartStyle";
import { buildAxisTickPlan, formatBucketTick } from "./historyChart";
import { useChartZoom } from "./useChartZoom";
import type { DateRange } from "./useHistoricalData";

const SERIES = [
  { key: "solarToBatteryKw", label: "Solar to battery", color: "solarToBattery", dashed: false },
  { key: "gridToBatteryKw", label: "Grid to battery", color: "gridToBattery", dashed: false },
  { key: "batteryToHouseKw", label: "Battery to house", color: "batteryToHouse", dashed: true },
  { key: "batteryToGridKw", label: "Battery to grid", color: "batteryToGrid", dashed: true },
] as const;

function BatteryChartLegend() {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-4 text-xs">
      <LegendSwatch shape="box" color={SWATCH_COLORS.batterySoc} label="Battery charge (%)" />
      {SERIES.map((s) => (
        <LegendSwatch key={s.key} shape="line" pattern={s.dashed ? "dashed" : undefined} color={SWATCH_COLORS[s.color]} label={`${s.label} (kW)`} />
      ))}
    </div>
  );
}

function BatteryTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ payload?: BatteryChartPoint }>;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point || label === undefined) {
    return null;
  }
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-lg">
      <p className="mb-1 text-ink-muted">{formatBucketTick(Number(label), "5min")}</p>
      {point.socPct !== null && (
        <LegendSwatch
          shape="box"
          color={SWATCH_COLORS.batterySoc}
          label={`Battery charge ${point.socPct.toFixed(0)}%`}
        />
      )}
      {SERIES.map((s) => (
        <LegendSwatch
          key={s.key}
          shape="line"
          pattern={s.dashed ? "dashed" : undefined}
          color={SWATCH_COLORS[s.color]}
          label={`${s.label} ${Math.abs(point[s.key]).toFixed(2)} kW`}
        />
      ))}
    </div>
  );
}

/**
 * Battery charge level (bars, 0 to 100%) over the day against the power flowing
 * into the battery from solar and the grid and out of it to the house and
 * the grid (lines, kW). Drag to zoom; the zoom and hover are shared with the
 * page's other today charts through `onZoom` and `syncId`.
 */
export function BatteryChart({
  data,
  range,
  onZoom,
  syncId,
}: {
  data: BatteryChartPoint[];
  range: DateRange;
  onZoom: (fromTs: number, toTs: number) => void;
  syncId?: string;
}) {
  const { handlers, selection } = useChartZoom(onZoom);
  const colors = useChartColors();
  const axisTickPlan = useMemo(() => buildAxisTickPlan(data, range), [data, range]);
  return (
    <>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} style={ZOOMABLE_CHART_STYLE} syncId={syncId} syncMethod="value" {...handlers}>
          <XAxis
            dataKey="ts"
            ticks={axisTickPlan.ticks}
            tickFormatter={(ts: number) => axisTickPlan.formatTick(Number(ts))}
            {...axisProps(colors)}
          />
          <YAxis yAxisId="pct" domain={[0, 100]} {...yAxisProps(colors, "%")} />
          <YAxis yAxisId="kw" {...yAxisProps(colors, "kW", "right")} />
          <Tooltip content={(props) => <BatteryTooltip {...props} />} />
          <ReferenceLine yAxisId="kw" y={0} stroke={colors.line} />
          <Bar
            yAxisId="pct"
            dataKey="socPct"
            name="Battery charge"
            fill={colors.batterySoc}
            radius={2}
            isAnimationActive={false}
          />
          {SERIES.map((s) => (
            <Line
              key={s.key}
              yAxisId="kw"
              type="linear"
              dataKey={s.key}
              name={s.label}
              stroke={colors[s.color]}
              strokeDasharray={s.dashed ? "4 3" : undefined}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          ))}
          <ZoomSelectionArea selection={selection} yAxisId="pct" />
        </ComposedChart>
      </ResponsiveContainer>
      <BatteryChartLegend />
    </>
  );
}
