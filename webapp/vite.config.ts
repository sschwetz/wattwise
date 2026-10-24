import { execSync } from 'node:child_process'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/** The commit being built: GIT_COMMIT from the Docker build, else the local
 * checkout's HEAD. "unknown" when neither is available. */
function buildCommit(): string {
  const fromEnv = process.env.GIT_COMMIT?.trim()
  if (fromEnv) return fromEnv.slice(0, 7)
  try {
    return execSync('git rev-parse --short=7 HEAD', { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
  } catch {
    return 'unknown'
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [tailwindcss(), react()],
  define: { __APP_COMMIT__: JSON.stringify(buildCommit()) },
})
