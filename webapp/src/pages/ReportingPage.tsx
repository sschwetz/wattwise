import { useMemo, useState } from "react";
import DatePicker from "react-datepicker";
import "react-datepicker/dist/react-datepicker.css";
import { useParams } from "react-router-dom";
import { ErrorAlert, ResetZoomButton } from "../dashboard/chartParts";
import { todayRange, zoomRangeFor } from "../dashboard/dateRange";
import { HistoryChart } from "../dashboard/GridCostChart";
import { useGridCostSeries } from "../dashboard/useGridCostSeries";
import { RANGE_MODE_LABELS, rangeForMode, type RangeMode } from "../dashboard/rangeModes";
import { signAccent } from "../dashboard/signColors";
import { Tile } from "../dashboard/Tile";
import type { ChartZoom } from "../dashboard/useChartZoom";
import { type DateRange, useBillingPeriodBounds, useCost } from "../dashboard/useHistoricalData";

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

const DATE_PICKER_INPUT_CLASS =
  "rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60";

function HistoryAndCost({ siteId }: { siteId: number }) {
  const [zoomRange, setZoomRange] = useState<DateRange | null>(null);
  const zoom: ChartZoom = { range: zoomRange, set: setZoomRange };
  const [rangeMode, setRangeMode] = useState<RangeMode>("today");
  const [customRange, setCustomRange] = useState<DateRange>(todayRange);
  const [method, setMethod] = useState<"net" | "gross">("net");
  const { data: billingRange } = useBillingPeriodBounds(siteId);
  const baseRange = useMemo(() => rangeForMode(rangeMode, customRange, billingRange), [rangeMode, customRange, billingRange]);
  const range = zoom.range ?? baseRange;
  const data = useGridCostSeries(siteId, range, method);
  const { data: cost, isError: costFailed } = useCost(siteId, range, method);
  const costTileAccent = cost ? signAccent(cost.total_cost_c) : undefined;
  const rangeModes = (Object.keys(RANGE_MODE_LABELS) as RangeMode[]).filter(
    (mode) => mode !== "billing" || billingRange || rangeMode === "billing",
  );

  // Picking a different range drops any zoom, so the new range is what shows.
  function changeRange(update: () => void) {
    zoom.set(null);
    update();
  }

  return (
    <div className="rounded-xl border border-line bg-surface p-5">
      <div className="flex flex-wrap items-center gap-4">
        <select
          value={rangeMode}
          onChange={(e) => changeRange(() => setRangeMode(e.target.value as RangeMode))}
          aria-label="Date range"
          className={DATE_PICKER_INPUT_CLASS}
        >
          {rangeModes.map((mode) => (
            <option key={mode} value={mode}>
              {RANGE_MODE_LABELS[mode]}
            </option>
          ))}
        </select>
        {rangeMode === "custom" && (
          <>
            <DatePicker
              selected={customRange.from}
              onChange={(date: Date | null) =>
                date && changeRange(() => setCustomRange((prev) => ({ ...prev, from: date })))
              }
              showTimeSelect
              timeIntervals={15}
              dateFormat="yyyy-MM-dd HH:mm"
              aria-label="Custom range start"
              className={DATE_PICKER_INPUT_CLASS}
            />
            <span className="text-sm text-ink-muted">to</span>
            <DatePicker
              selected={customRange.to}
              onChange={(date: Date | null) =>
                date && changeRange(() => setCustomRange((prev) => ({ ...prev, to: date })))
              }
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
        {zoom.range && <ResetZoomButton onClick={() => zoom.set(null)} />}
      </div>
      <div className="mt-4 space-y-1" data-testid="history-cost">
        {costFailed && <ErrorAlert message="Failed to load cost." />}
        {data.readingsFailed && <ErrorAlert message="Failed to load historical readings." />}
        {data.costSeriesFailed && <ErrorAlert message="Failed to load the cost trend." />}
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
          importReadings={data.importReadings}
          exportReadings={data.exportReadings}
          costSeries={data.costSeries}
          range={range}
          bucket={data.bucket}
          onZoom={(fromTs, toTs) => zoom.set(zoomRangeFor(fromTs, toTs, data.bucket))}
        />
      </div>
    </div>
  );
}

export function ReportingPage() {
  const { siteId } = useParams<{ siteId: string }>();

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <h1 className="font-display text-2xl font-semibold text-ink">Reporting</h1>
      <HistoryAndCost siteId={Number(siteId)} />
    </div>
  );
}
