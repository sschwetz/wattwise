import { BUCKET_STEP_MS, bucketStartFromMs, bucketStartMs, enumerateBucketStarts } from "./historyChart";
import type { DateRange } from "./useHistoricalData";
import type { Reading } from "./useLiveReadings";

interface BatteryChartInputs {
  soc: Reading[];
  charge: Reading[];
  discharge: Reading[];
  production: Reading[];
  consumption: Reading[];
  gridExport: Reading[];
}

export interface BatteryChartPoint {
  ts: number;
  /** Battery state of charge at the end of the bucket, 0 to 100; null with no SOC data. */
  socPct: number | null;
  solarToBatteryKw: number;
  gridToBatteryKw: number;
  batteryToHouseKw: number;
  /** Negative: power leaving the battery for the grid plots below zero, like grid export. */
  batteryToGridKw: number;
}

const BUCKET = "5min";
const MINUTES_PER_BUCKET = BUCKET_STEP_MS[BUCKET] / 60_000;

interface Flows {
  solarToBattery: number;
  gridToBattery: number;
  batteryToHouse: number;
  batteryToGrid: number;
}

const FLOW_KEYS = ["solarToBattery", "gridToBattery", "batteryToHouse", "batteryToGrid"] as const;

function byMinute(readings: Reading[]): Map<number, number> {
  return new Map(readings.map((r) => [new Date(r.ts).getTime(), r.value]));
}

/**
 * Splits one minute's battery charge and discharge by where the energy came
 * from or went. The collectors report totals, not sources, so this is an
 * estimate: charging takes solar surplus (production minus consumption) first
 * and the rest comes from the grid; discharging goes to the grid up to the
 * minute's export, and the rest to the house.
 */
export function splitBatteryFlows(
  chargeW: number,
  dischargeW: number,
  productionW: number,
  consumptionW: number,
  gridExportW: number,
): Flows {
  const solarToBattery = Math.min(chargeW, Math.max(productionW - consumptionW, 0));
  const batteryToGrid = Math.min(dischargeW, gridExportW);
  return {
    solarToBattery,
    gridToBattery: chargeW - solarToBattery,
    batteryToHouse: dischargeW - batteryToGrid,
    batteryToGrid,
  };
}

/** Last SOC value in each bucket, by bucket start. */
function lastSocByBucket(soc: Reading[]): Map<number, number> {
  const last = new Map<number, number>();
  for (const r of [...soc].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0))) {
    last.set(bucketStartMs(r.ts, BUCKET), r.value);
  }
  return last;
}

type FlowKw = Pick<BatteryChartPoint, `${keyof Flows}Kw`>;

const zeroFlowKw = (): FlowKw => ({ solarToBatteryKw: 0, gridToBatteryKw: 0, batteryToHouseKw: 0, batteryToGridKw: 0 });

/** Adds each minute's estimated flows into its 5-minute bucket as average kW. */
function averageFlowsByBucket(inputs: BatteryChartInputs): Map<number, FlowKw> {
  const production = byMinute(inputs.production);
  const consumption = byMinute(inputs.consumption);
  const gridExport = byMinute(inputs.gridExport);
  const charge = byMinute(inputs.charge);
  const discharge = byMinute(inputs.discharge);
  const buckets = new Map<number, FlowKw>();
  for (const ts of new Set([...charge.keys(), ...discharge.keys()])) {
    const flows = splitBatteryFlows(
      charge.get(ts) ?? 0,
      discharge.get(ts) ?? 0,
      production.get(ts) ?? 0,
      consumption.get(ts) ?? 0,
      gridExport.get(ts) ?? 0,
    );
    const key = bucketStartFromMs(ts, BUCKET);
    let total = buckets.get(key);
    if (!total) {
      total = zeroFlowKw();
      buckets.set(key, total);
    }
    for (const k of FLOW_KEYS) {
      total[`${k}Kw`] += flows[k] / 1000 / MINUTES_PER_BUCKET;
    }
  }
  return buckets;
}

export function buildBatteryChartData(inputs: BatteryChartInputs, range: DateRange): BatteryChartPoint[] {
  const starts = enumerateBucketStarts(range, BUCKET);
  const soc = lastSocByBucket(inputs.soc);
  const flows = averageFlowsByBucket(inputs);
  return starts.map((ts) => {
    const flow = flows.get(ts) ?? zeroFlowKw();
    return { ts, socPct: soc.get(ts) ?? null, ...flow, batteryToGridKw: 0 - flow.batteryToGridKw };
  });
}
