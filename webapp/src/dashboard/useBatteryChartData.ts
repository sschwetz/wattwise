import { useMemo } from "react";
import { buildBatteryChartData } from "./batteryFlows";
import { type DateRange, useHistoricalEnergyReadings } from "./useHistoricalData";

/** Battery chart points for `range`, at 5-minute resolution. */
export function useBatteryChartData(siteId: number, range: DateRange) {
  const soc = useHistoricalEnergyReadings(siteId, range, "battery_soc_pct");
  const charge = useHistoricalEnergyReadings(siteId, range, "battery_charge_w");
  const discharge = useHistoricalEnergyReadings(siteId, range, "battery_discharge_w");
  const production = useHistoricalEnergyReadings(siteId, range, "production_w");
  const consumption = useHistoricalEnergyReadings(siteId, range, "consumption_w");
  const gridExport = useHistoricalEnergyReadings(siteId, range, "grid_export_w");
  const queries = [soc, charge, discharge, production, consumption, gridExport];
  const data = useMemo(
    () =>
      buildBatteryChartData(
        {
          soc: soc.data ?? [],
          charge: charge.data ?? [],
          discharge: discharge.data ?? [],
          production: production.data ?? [],
          consumption: consumption.data ?? [],
          gridExport: gridExport.data ?? [],
        },
        range,
      ),
    [soc.data, charge.data, discharge.data, production.data, consumption.data, gridExport.data, range],
  );
  return { data, failed: queries.some((q) => q.isError), loading: queries.some((q) => q.isLoading) };
}
