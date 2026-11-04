import type { ActorInstance, ActorSchedule } from "./types";
import { cardClass } from "./styles";

export interface ScheduleDetailProps {
  siteId: number;
  /** Null while a new schedule is being started. */
  schedule: ActorSchedule | null;
  instance: ActorInstance;
  canEdit: boolean;
}

/** Placeholder: the editor, unit state and run history replace this body. */
export function ScheduleDetail({ schedule }: ScheduleDetailProps) {
  return (
    <section className={`${cardClass} space-y-2`}>
      <h2 className="text-lg font-medium text-ink">{schedule ? schedule.name : "New schedule"}</h2>
      <p className="text-sm text-ink-muted">Editor loading...</p>
    </section>
  );
}
