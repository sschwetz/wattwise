const REPO_URL = "https://github.com/sschwetz/wattwise";

/** Short hash of the commit this build was made from. Injected at build time
 * (see vite.config.ts); "dev" when it was not, as in tests. */
export const BUILD_COMMIT: string = typeof __APP_COMMIT__ === "string" ? __APP_COMMIT__ : "dev";

/** GitHub page for a commit, or null when the value is not a hex commit hash
 * (as with "dev" or "unknown"). */
export function commitUrl(commit: string): string | null {
  return /^[0-9a-f]{7,40}$/.test(commit) ? `${REPO_URL}/commit/${commit}` : null;
}

/** GitHub page for the running commit, or null for a build with no commit. */
export const BUILD_COMMIT_URL: string | null = commitUrl(BUILD_COMMIT);
