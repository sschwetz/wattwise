import type { Capabilities, DeviceStatePayload } from "./types";
import type { ScheduleFormState } from "./scheduleForm";
import type { FormErrors } from "./scheduleValidation";

/** What every editor step gets: the whole form, a setter, the current errors and the device's declaration. */
export interface EditorModel {
  form: ScheduleFormState;
  setForm: (next: ScheduleFormState) => void;
  errors: FormErrors;
  capabilities: Capabilities;
  canEdit: boolean;
  /** The unit's latest readings, or null before the actor has reported any. */
  readings: DeviceStatePayload | null;
}

/** "C" is sent by the actor for degrees Celsius. */
export function formatUnit(unit: string | null): string {
  if (unit === null) return "";
  return unit === "C" ? "°C" : unit;
}

/** Props for a control that may carry a validation message. */
export function invalidProps(id: string, message: string | undefined) {
  return message ? { "aria-invalid": true, "aria-describedby": id } : {};
}
