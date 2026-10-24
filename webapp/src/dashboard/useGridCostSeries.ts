import { bucketForRange } from "./rangeModes";
import { type DateRange, useCostSeries, useHistoricalEnergyReadings } from "./useHistoricalData";

/** The chart's bar metrics: actual grid flow, regardless of the net/gross cost method. */
const CHART_IMPORT_METRIC = "grid_import_w";
const CHART_EXPORT_METRIC = "grid_export_w";

/** The grid bars and cost line inputs for a range: import/export energy plus
 * the cost trend at the bucket size that fits the range. */
export function useGridCostSeries(siteId: number, range: DateRange, method: "net" | "gross") {
  const bucket = bucketForRange(range);
  const importHistory = useHistoricalEnergyReadings(siteId, range, CHART_IMPORT_METRIC);
  const exportHistory = useHistoricalEnergyReadings(siteId, range, CHART_EXPORT_METRIC);
  const costSeries = useCostSeries(siteId, range, method, bucket);
  return {
    bucket,
    importReadings: importHistory.data ?? [],
    exportReadings: exportHistory.data ?? [],
    costSeries: costSeries.data ?? [],
    readingsFailed: importHistory.isError || exportHistory.isError,
    costSeriesFailed: costSeries.isError,
    loading: importHistory.isLoading || exportHistory.isLoading || costSeries.isLoading,
  };
}
