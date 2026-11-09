import { commitUrl } from "../buildInfo";

/** A commit hash in monospace, linked to it on GitHub when it is a real hash. */
export function CommitLink({ commit, className = "" }: { commit: string; className?: string }) {
  const url = commitUrl(commit);
  if (url === null) return <span className="font-mono">{commit}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className={`font-mono hover:underline ${className}`.trim()}>
      {commit}
    </a>
  );
}
