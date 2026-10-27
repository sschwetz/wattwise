import { SHORT_DAYS, SHORT_MONTHS } from "../sources/rateWindowUi";

export const MONTH_OPTIONS = SHORT_MONTHS.map((name, i) => ({ value: i + 1, label: name }));
export const DAY_OPTIONS = SHORT_DAYS.map((name, i) => ({ value: i, label: name }));
