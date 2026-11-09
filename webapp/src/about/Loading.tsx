import { Spinner } from "../components/Spinner";

export function Loading() {
  return (
    <p className="flex items-center gap-2 p-3 text-sm text-ink-muted">
      <Spinner /> Loading...
    </p>
  );
}
