export function AccessNote({ note }: { note: string | null }) {
  if (!note) return null;
  return <p className="text-sm text-ink-muted">{note}</p>;
}
