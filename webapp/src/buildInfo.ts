const REPO_URL = "https://github.com/sschwetz/wattwise";

/** Short hash of the commit this build was made from. Injected at build time
 * (see vite.config.ts); "dev" when it was not, as in tests. */
export const BUILD_COMMIT: string = typeof __APP_COMMIT__ === "string" ? __APP_COMMIT__ : "dev";

/** GitHub page for the running commit, or null for a build with no commit. */
export const BUILD_COMMIT_URL: string | null = /^[0-9a-f]{7,40}$/.test(BUILD_COMMIT)
  ? `${REPO_URL}/commit/${BUILD_COMMIT}`
  : null;
