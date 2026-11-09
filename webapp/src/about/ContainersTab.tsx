import { CommitLink } from "../components/CommitLink";
import { QueryError } from "../components/QueryError";
import { Loading } from "./Loading";
import { type ServiceStatus, useServices } from "./useServices";

function reportedText(lastReportedAt: string | null): string {
  return lastReportedAt ? `last reported ${new Date(lastReportedAt).toLocaleString()}` : "never reported";
}

function ServiceRow({ service }: { service: ServiceStatus }) {
  return (
    <li className="flex items-start justify-between gap-4 px-3 py-2 text-sm">
      <span className="font-medium text-ink">{service.service}</span>
      <span className="text-right text-ink">
        {!service.reporting && <span className="mr-2 text-ink-muted">not reporting</span>}
        {service.commit === null ? <span className="text-ink-muted">-</span> : <CommitLink commit={service.commit} />}
        <span className="block text-xs text-ink-muted">{reportedText(service.last_reported_at)}</span>
      </span>
    </li>
  );
}

export function ContainersTab() {
  const { data, isError, error, refetch } = useServices();
  if (data) {
    return (
      <ul className="divide-y divide-line rounded-md border border-line">
        {data.map((service) => (
          <ServiceRow key={service.service} service={service} />
        ))}
      </ul>
    );
  }
  if (isError) {
    return (
      <div className="p-3">
        <QueryError error={error} fallback="Could not load container status." onRetry={() => void refetch()} />
      </div>
    );
  }
  return <Loading />;
}
