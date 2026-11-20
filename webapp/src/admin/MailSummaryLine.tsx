import { QueryError } from "../components/QueryError";
import { mutedClass } from "./adminUi";
import { useOutboxSummary } from "./useAdminMail";

export function MailSummaryLine() {
  const { data, isLoading, isError, error, refetch } = useOutboxSummary();
  if (isError && !data) {
    return <QueryError error={error} fallback="Could not load the email summary." onRetry={() => void refetch()} />;
  }
  if (isLoading || !data) return <p className={mutedClass}>Loading...</p>;
  return (
    <div className="space-y-1">
      <p className="text-sm text-ink">
        Pending: {data.pending} · Sent (24 h): {data.sent_24h} · Failed (24 h): {data.failed_24h}
      </p>
      {data.last_error !== null && <p className={mutedClass}>Last error: {data.last_error}</p>}
    </div>
  );
}
