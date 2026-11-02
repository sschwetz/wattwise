import { Area, ComposedChart, Line, type MouseHandlerDataParam, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { LegendSwatch } from "../dashboard/chartParts";
import { axisProps, SWATCH_COLORS, useChartColors, yAxisProps } from "../dashboard/chartStyle";
import type { TotalsBucket, TotalsRow } from "../dashboard/useHistoricalData";
import { formatRowTime } from "./totalsFormat";

interface ChartPoint {
  ts: number;
  label: string;
  socPct: number | null;
  production: number;
  consumption: number;
  gridImport: number;
  gridExport: number;
  batteryCharge: number;
  batteryDischarge: number;
}

type LineKey = "production" | "consumption" | "gridImport" | "gridExport" | "batteryCharge" | "batteryDischarge";

const LINES: { key: LineKey; label: string; color: keyof typeof SWATCH_COLORS; dashed?: boolean }[] = [
  { key: "production", label: "Production", color: "production" },
  { key: "consumption", label: "Consumption", color: "consumption" },
  { key: "gridImport", label: "Grid import", color: "gridImport" },
  { key: "gridExport", label: "Grid export", color: "gridExport" },
  { key: "batteryCharge", label: "Battery import", color: "batteryCharge" },
  { key: "batteryDischarge", label: "Battery export", color: "batteryDischarge", dashed: true },
];

/** Average power over each row: its energy divided by the hours it covers. */
function toChartPoints(rows: TotalsRow[], bucket: TotalsBucket): ChartPoint[] {
  return rows.map((row) => ({
    ts: new Date(row.start).getTime(),
    label: formatRowTime(row, bucket),
    socPct: row.battery_soc_pct,
    production: row.production_kwh / row.hours,
    consumption: row.consumption_kwh / row.hours,
    gridImport: row.grid_import_kwh / row.hours,
    gridExport: row.grid_export_kwh / row.hours,
    batteryCharge: row.battery_charge_kwh / row.hours,
    batteryDischarge: row.battery_discharge_kwh / row.hours,
  }));
}

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: ChartPoint }>;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) {
    return null;
  }
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-lg">
      <p className="mb-1 text-ink-muted">{point.label}</p>
      {point.socPct !== null && (
        <LegendSwatch shape="box" color={SWATCH_COLORS.batterySoc} label={`Battery charge ${point.socPct.toFixed(0)}%`} />
      )}
      {LINES.map((line) => (
        <LegendSwatch
          key={line.key}
          shape="line"
          pattern={line.dashed ? "dashed" : undefined}
          color={SWATCH_COLORS[line.color]}
          label={`${line.label} ${point[line.key].toFixed(2)} kW`}
        />
      ))}
    </div>
  );
}

function ChartLegend() {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-4 text-xs">
      <LegendSwatch shape="box" color={SWATCH_COLORS.batterySoc} label="Battery charge (%)" />
      {LINES.map((line) => (
        <LegendSwatch
          key={line.key}
          shape="line"
          pattern={line.dashed ? "dashed" : undefined}
          color={SWATCH_COLORS[line.color]}
          label={`${line.label} (kW)`}
        />
      ))}
    </div>
  );
}

/**
 * Average power (lines, kW) and battery charge level (area, 0 to 100%) for
 * each day, hour or minute in the table below it. Clicking a day or an hour
 * calls `onSelectRow` with that row, as clicking its table row does; minutes
 * are the lowest level, so clicking one does nothing.
 */
export function TotalsChart({
  rows,
  bucket,
  onSelectRow,
}: {
  rows: TotalsRow[];
  bucket: TotalsBucket;
  onSelectRow: (row: TotalsRow) => void;
}) {
  const colors = useChartColors();
  const data = toChartPoints(rows, bucket);
  const drillable = bucket !== "minute";
  const handleClick = ({ activeTooltipIndex }: MouseHandlerDataParam) => {
    const row = typeof activeTooltipIndex === "number" ? rows[activeTooltipIndex] : undefined;
    if (row) {
      onSelectRow(row);
    }
  };
  return (
    <div data-testid="totals-chart">
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart
          data={data}
          style={drillable ? { cursor: "pointer" } : undefined}
          onClick={drillable ? handleClick : undefined}
        >
          <XAxis dataKey="label" minTickGap={24} {...axisProps(colors)} />
          <YAxis yAxisId="pct" domain={[0, 100]} {...yAxisProps(colors, "%")} />
          <YAxis yAxisId="kw" {...yAxisProps(colors, "kW", "right")} />
          <Tooltip content={(props) => <ChartTooltip {...props} />} />
          <Area
            yAxisId="pct"
            type="monotone"
            dataKey="socPct"
            name="Battery charge"
            fill={colors.batterySoc}
            stroke={colors.batterySoc}
            fillOpacity={1}
            isAnimationActive={false}
          />
          {LINES.map((line) => (
            <Line
              key={line.key}
              yAxisId="kw"
              type="linear"
              dataKey={line.key}
              name={line.label}
              stroke={colors[line.color]}
              strokeDasharray={line.dashed ? "4 3" : undefined}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
      <ChartLegend />
    </div>
  );
}
