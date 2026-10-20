import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { LIVE_POLL_MS } from "../dashboard/useLiveReadings";

export interface SiteTariff {
  id: number;
  site_id: number;
  effective_from: string;
  effective_to: string | null;
  // Serialized by core-api as a decimal string, not a JSON number.
  service_availability_c_per_day: string;
  // Serialized by core-api as a decimal string, not a JSON number. null if
  // this tariff has no monthly fee configured.
  monthly_fee_c: string | null;
}

export interface MonthlyFeeDiscountWindow {
  id: number;
  site_tariff_id: number;
  label: string;
  starts_at: string;
  ends_at: string;
  // Serialized by core-api as a decimal string, not a JSON number.
  discounted_fee_c: string;
}

export interface DemandWindow {
  id: number;
  site_tariff_id: number;
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  // Serialized by core-api as a decimal string, not a JSON number.
  rate_c_per_kw_per_day: string;
  measurement_interval_minutes: number;
}

export interface ExportAdjustmentWindow {
  id: number;
  site_tariff_id: number;
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  // Serialized by core-api as a decimal string, not a JSON number. Signed:
  // positive is an export incentive, negative is a disincentive.
  adjustment_c_per_kwh: string;
  // Decimal string. kWh of exports per day, accumulating through the billing
  // period, that are exempt from the adjustment. "0.0000" means no allowance.
  daily_allowance_kwh: string;
}

export interface ConditionalCreditWindow {
  id: number;
  site_tariff_id: number;
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  // Serialized by core-api as decimal strings, not JSON numbers.
  max_avg_import_w: string;
  credit_c: string;
}

export interface BillingCycle {
  id: number;
  site_id: number;
  effective_from: string;
  effective_to: string | null;
  cycle_months: number;
  anchor_day: number;
}

export interface DemandCharge {
  id: number;
  billing_period_start: string;
  billing_period_end: string;
  // Serialized by core-api as a decimal string, not a JSON number.
  charge_amount: string;
}

interface CreateTariffInput {
  effective_from: string;
  service_availability_c_per_day: number;
  monthly_fee_c?: number;
}

interface CreateMonthlyFeeDiscountWindowInput {
  label: string;
  starts_at: string;
  ends_at: string;
  discounted_fee_c: number;
}

interface CreateDemandWindowInput {
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  rate_c_per_kw_per_day: number;
}

interface CreateExportAdjustmentWindowInput {
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  adjustment_c_per_kwh: number;
  daily_allowance_kwh: number;
}

interface CreateConditionalCreditWindowInput {
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  max_avg_import_w: number;
  credit_c: number;
}

interface CreateBillingCycleInput {
  effective_from: string;
  cycle_months: number;
  anchor_day: number;
}

export function useTariffs(siteId: number) {
  return useQuery({
    queryKey: ["tariffs", siteId],
    queryFn: () => apiFetch<SiteTariff[]>(`/api/v1/sites/${siteId}/tariffs`),
  });
}

export function useCreateTariff(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateTariffInput) =>
      apiFetch<SiteTariff>(`/api/v1/sites/${siteId}/tariffs`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tariffs", siteId] });
    },
  });
}

export function useEndTariff(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tariffId: number) =>
      apiFetch<SiteTariff>(`/api/v1/sites/${siteId}/tariffs/${tariffId}`, {
        method: "PATCH",
        body: JSON.stringify({ effective_to: new Date().toISOString() }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tariffs", siteId] });
    },
  });
}

export function useDemandWindows(siteId: number, tariffId: number) {
  return useQuery({
    queryKey: ["demand-windows", siteId, tariffId],
    queryFn: () =>
      apiFetch<DemandWindow[]>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/demand-windows`),
  });
}

export function useCreateDemandWindow(siteId: number, tariffId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateDemandWindowInput) =>
      apiFetch<DemandWindow>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/demand-windows`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["demand-windows", siteId, tariffId] });
    },
  });
}

export function useExportAdjustmentWindows(siteId: number, tariffId: number) {
  return useQuery({
    queryKey: ["export-adjustments", siteId, tariffId],
    queryFn: () =>
      apiFetch<ExportAdjustmentWindow[]>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/export-adjustments`),
  });
}

export function useCreateExportAdjustmentWindow(siteId: number, tariffId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateExportAdjustmentWindowInput) =>
      apiFetch<ExportAdjustmentWindow>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/export-adjustments`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["export-adjustments", siteId, tariffId] });
    },
  });
}

export function useConditionalCreditWindows(siteId: number, tariffId: number) {
  return useQuery({
    queryKey: ["conditional-credits", siteId, tariffId],
    queryFn: () =>
      apiFetch<ConditionalCreditWindow[]>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/conditional-credits`),
  });
}

export function useCreateConditionalCreditWindow(siteId: number, tariffId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateConditionalCreditWindowInput) =>
      apiFetch<ConditionalCreditWindow>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/conditional-credits`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conditional-credits", siteId, tariffId] });
    },
  });
}

export function useMonthlyFeeDiscountWindows(siteId: number, tariffId: number) {
  return useQuery({
    queryKey: ["monthly-fee-discounts", siteId, tariffId],
    queryFn: () =>
      apiFetch<MonthlyFeeDiscountWindow[]>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/monthly-fee-discounts`),
  });
}

export function useCreateMonthlyFeeDiscountWindow(siteId: number, tariffId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateMonthlyFeeDiscountWindowInput) =>
      apiFetch<MonthlyFeeDiscountWindow>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/monthly-fee-discounts`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["monthly-fee-discounts", siteId, tariffId] });
    },
  });
}

export function useBillingCycles(siteId: number) {
  return useQuery({
    queryKey: ["billing-cycles", siteId],
    queryFn: () => apiFetch<BillingCycle[]>(`/api/v1/sites/${siteId}/billing-cycle`),
  });
}

export function useCreateBillingCycle(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateBillingCycleInput) =>
      apiFetch<BillingCycle>(`/api/v1/sites/${siteId}/billing-cycle`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["billing-cycles", siteId] });
    },
  });
}

export function useEndBillingCycle(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (cycleId: number) =>
      apiFetch<BillingCycle>(`/api/v1/sites/${siteId}/billing-cycle/${cycleId}`, {
        method: "PATCH",
        body: JSON.stringify({ effective_to: new Date().toISOString() }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["billing-cycles", siteId] });
    },
  });
}

export function useDemandCharges(siteId: number) {
  return useQuery({
    queryKey: ["demand-charges", siteId],
    queryFn: () => apiFetch<DemandCharge[]>(`/api/v1/sites/${siteId}/demand-charges`),
  });
}

/**
 * The DemandWindow active right now, if any -- polled like the dashboard's
 * other live tiles so the dashboard's "DEMAND WINDOW ACTIVE" notice tracks
 * a window starting/ending without a page reload. The backend 404s (code
 * "no_active_demand_window") when none matches; callers treat that as
 * "nothing active", not an error.
 */
export function useActiveDemandWindow(siteId: number) {
  return useQuery({
    queryKey: ["demand-window", siteId, "active"],
    queryFn: () => apiFetch<DemandWindow>(`/api/v1/sites/${siteId}/demand-window/active`),
    refetchInterval: LIVE_POLL_MS,
  });
}
