import { commitUrl } from "../buildInfo";
import { type ServiceStatus, useServices } from "./useServices";

function CommitLink({ commit }: { commit: string | null }) {
  if (commit === null) return <span className="text-ink-muted">-</span>;
  const url = commitUrl(commit);
  if (url === null) return <span className="font-mono">{commit}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className="font-mono hover:underline">
      {commit}
    </a>
  );
}

function reportedText(lastReportedAt: string | null): string {
  return lastReportedAt ? `last reported ${new Date(lastReportedAt).toLocaleString()}` : "never reported";
}

function ServiceRow({ service }: { service: ServiceStatus }) {
  return (
    <li className="flex items-start justify-between gap-4 px-3 py-2 text-sm">
      <span className="font-medium text-ink">{service.service}</span>
      <span className="text-right text-ink">
        {!service.reporting && <span className="mr-2 text-ink-muted">not reporting</span>}
        <CommitLink commit={service.commit} />
        <span className="block text-xs text-ink-muted">{reportedText(service.last_reported_at)}</span>
      </span>
    </li>
  );
}

export function ContainersTab() {
  const { data, isError } = useServices(true);
  if (data) {
    return (
      <ul className="divide-y divide-line rounded-md border border-line">
        {data.map((service) => (
          <ServiceRow key={service.service} service={service} />
        ))}
      </ul>
    );
  }
  if (isError) return <p role="alert" className="p-3 text-sm text-danger">Could not load container status.</p>;
  return <p className="p-3 text-sm text-ink-muted">Loading...</p>;
}
