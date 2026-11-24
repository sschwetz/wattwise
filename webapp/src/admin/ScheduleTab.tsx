import { QueryError } from "../components/QueryError";
import { JobRow } from "./JobRow";
import { headingClass, mutedClass, sectionClass } from "./adminUi";
import { useAdminJobs } from "./useAdminJobs";

export function ScheduleTab() {
  const { data, isLoading, isError, error, refetch } = useAdminJobs();
  if (isError && !data) {
    return <QueryError error={error} fallback="Could not load the job schedules." onRetry={() => void refetch()} />;
  }
  if (isLoading || !data) return <p className={mutedClass}>Loading...</p>;
  return (
    <section className={sectionClass}>
      <h2 className={headingClass}>Background jobs</h2>
      {!data.scheduler_running && (
        <p className={mutedClass}>The scheduler is off. Changes are saved and apply when it is enabled.</p>
      )}
      <div className="divide-y divide-line">
        {data.jobs.map((job) => (
          <JobRow key={job.id} job={job} timezone={data.timezone} />
        ))}
      </div>
    </section>
  );
}
