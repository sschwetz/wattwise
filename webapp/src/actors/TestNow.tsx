import { errorMessage } from "../api/client";
import { Spinner } from "../components/Spinner";
import { noticeClass, secondaryButtonClass, hintClass } from "./styles";
import { useTestSchedule } from "./useActors";

/** Queues a test run of a saved schedule. The history card shows it once the server has it. */
export function TestNow({ siteId, scheduleId }: { siteId: number; scheduleId: number }) {
  const test = useTestSchedule(siteId);
  return (
    <div className="space-y-2">
      <button type="button" disabled={test.isPending} onClick={() => test.mutate(scheduleId)} className={`${secondaryButtonClass} inline-flex items-center gap-2`}>
        {test.isPending && <Spinner />}
        Test now
      </button>
      <p className={hintClass}>
        Runs this schedule's action now, ignoring its trigger and conditions, and puts the unit back after a short pause if the schedule restores.
      </p>
      {test.isSuccess && <p role="status" className="text-sm text-ink">Test queued. The actor starts it within about 30 seconds.</p>}
      {test.isError && <p role="alert" className={noticeClass}>{errorMessage(test.error, "Could not start the test.")}</p>}
    </div>
  );
}
