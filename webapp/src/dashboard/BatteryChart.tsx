import { useMemo } from "react";
import { Bar, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { BatteryChartPoint } from "./batteryFlows";
import { ZoomSelectionArea } from "./chartParts";
import { SWATCH_COLORS, ZOOMABLE_CHART_STYLE } from "./chartStyle";
import { buildAxisTickPlan, formatBucketTick } from "./historyChart";
import { useChartZoom } from "./useChartZoom";
import type { DateRange } from "./useHistoricalData";

const SERIES = [
  { key: "solarToBatteryKw", label: "Solar to battery", color: SWATCH_COLORS.solarToBattery, dashed: false },
  { key: "gridToBatteryKw", label: "Grid to battery", color: SWATCH_COLORS.gridToBattery, dashed: false },
  { key: "batteryToHouseKw", label: "Battery to house", color: SWATCH_COLORS.batteryToHouse, dashed: true },
  { key: "batteryToGridKw", label: "Battery to grid", color: SWATCH_COLORS.batteryToGrid, dashed: true },
] as const;

function Swatch({ color, label, line, dashed }: { color: string; label: string; line?: boolean; dashed?: boolean }) {
  const style = dashed
    ? { backgroundImage: `repeating-linear-gradient(to right, ${color} 0 4px, transparent 4px 7px)` }
    : { backgroundColor: color };
  return (
    <span className="flex items-center gap-1.5">
      <span aria-hidden className={line ? "h-0.5 w-4" : "h-2.5 w-2.5 rounded-sm"} style={style} />
      <span className="text-ink-muted">{label}</span>
    </span>
  );
}

function BatteryChartLegend() {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-4 text-xs">
      <Swatch color={SWATCH_COLORS.batterySocChange} label="Battery change (%)" />
      {SERIES.map((s) => (
        <Swatch key={s.key} color={s.color} label={`${s.label} (kW)`} line dashed={s.dashed} />
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
      {point.socChangePct !== null && (
        <Swatch
          color={SWATCH_COLORS.batterySocChange}
          label={`Battery change ${point.socChangePct > 0 ? "+" : ""}${point.socChangePct.toFixed(1)}%`}
        />
      )}
      {SERIES.map((s) => (
        <Swatch key={s.key} color={s.color} label={`${s.label} ${point[s.key].toFixed(2)} kW`} line dashed={s.dashed} />
      ))}
    </div>
  );
}

/**
 * Battery charge change per 5 minutes (bars, %) against the power flowing
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
  const axisTickPlan = useMemo(() => buildAxisTickPlan(data, range), [data, range]);
  return (
    <>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} style={ZOOMABLE_CHART_STYLE} syncId={syncId} syncMethod="value" {...handlers}>
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
            yAxisId="pct"
            stroke="var(--color-ink-muted)"
            fontSize={12}
            tickLine={false}
            axisLine={false}
            width={48}
            label={{
              value: "%",
              angle: -90,
              position: "insideLeft",
              style: { fill: "var(--color-ink-muted)", fontSize: 12, textAnchor: "middle" },
            }}
          />
          <YAxis
            yAxisId="kw"
            orientation="right"
            stroke="var(--color-ink-muted)"
            fontSize={12}
            tickLine={false}
            axisLine={false}
            width={48}
            label={{
              value: "kW",
              angle: -90,
              position: "insideRight",
              style: { fill: "var(--color-ink-muted)", fontSize: 12, textAnchor: "middle" },
            }}
          />
          <Tooltip content={(props) => <BatteryTooltip {...props} />} />
          <Bar
            yAxisId="pct"
            dataKey="socChangePct"
            name="Battery change"
            fill={SWATCH_COLORS.batterySocChange}
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
              stroke={s.color}
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
