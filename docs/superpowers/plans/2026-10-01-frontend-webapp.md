# Frontend Webapp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the React/Vite/TypeScript SPA for sub-project 5 — login (with mandatory
TOTP and password reset), site picker, a live+historical dashboard, and source/billing
management screens — talking only to Core API over HTTP.

**Architecture:** Pages are built bottom-up: a typed API client and auth context first
(no UI depends on routing yet), then each page as an isolated, independently-tested
component (mocking the API client, using `MemoryRouter` for navigation assertions), and
only in the final task are they assembled into the real route tree. This avoids any task
importing a file a later task hasn't created yet. State fetching goes through TanStack
Query everywhere so loading/error/caching behavior is consistent across the app rather
than hand-rolled per page.

**Tech Stack:** React 18, Vite, TypeScript (strict), Tailwind CSS, React Router,
TanStack Query, Recharts, `qrcode`, Vitest + React Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-01-frontend-webapp-design.md`

**Depends on:** `docs/superpowers/plans/2026-10-01-core-api-auth-mfa-password-reset.md`
being implemented first — every page in this plan calls an endpoint that plan adds
(`/auth/register`, the two-step `/auth/login`, `/auth/totp/*`, `/auth/password-reset/*`,
`PATCH /sites/{id}`).

## Global Constraints

- The API base URL is read at runtime from `window.__APP_CONFIG__.apiBaseUrl` (populated
  by `/config.js`, written by the container entrypoint from an env var) — never baked
  into the build. No component calls `fetch` directly; everything goes through
  `apiFetch` from `src/api/client.ts`.
- `access_token` persists in `localStorage`. `setup_token`/`mfa_token` (the TOTP-flow
  scoped tokens) are passed only via React Router navigation state, in memory, and are
  never written to `localStorage` — a page refresh during TOTP setup/verify loses them
  by design.
- A `401` from any authenticated request clears the stored `access_token` and redirects
  to `/login` — implemented once, centrally, in `src/api/client.ts`, not re-implemented
  per page.
- No `any` in TypeScript anywhere — every API response gets a typed interface in the
  file that calls it (per this repo's `CLAUDE.md` review standard).
- Deployed as its own nginx-served Docker container, independent of Core API's container
  — per the spec's Boundaries section.

## Review Focus

- A `401` arrives mid-session (e.g. the token expired while the dashboard was open) —
  must clear the token and redirect to `/login`, not leave a broken/blank screen behind.
- `TotpSetupPage`/`TotpVerifyPage` rendered with no router state (direct URL visit, or a
  page refresh mid-flow — the scoped token lived only in memory and is now gone) — must
  show a clear "log in again" message, not crash on a `null` token.
- The site picker's three cases — zero sites, exactly one, two-or-more — each render
  correctly (onboarding form / straight to dashboard / switcher), since a wrong branch
  here blocks every other screen behind it.
- The dashboard's net/gross toggle and time-range selector actually change the
  TanStack Query key, so switching them re-fetches rather than silently showing stale
  data under new-looking controls.
- The billing screen's demand-charges list renders an explicit empty state when Core API
  returns `[]` (expected for now, since the background derivation job isn't built) —
  not an error state or an infinite loading spinner.

---

## Task 1: Project scaffold (Vite + React + TypeScript + Tailwind + Vitest)

**Files:**
- Create: `webapp/package.json`
- Create: `webapp/vite.config.ts`
- Create: `webapp/vitest.config.ts`
- Create: `webapp/tsconfig.json`
- Create: `webapp/tsconfig.node.json`
- Create: `webapp/tailwind.config.js`
- Create: `webapp/postcss.config.js`
- Create: `webapp/index.html`
- Create: `webapp/public/config.js`
- Create: `webapp/src/main.tsx`
- Create: `webapp/src/App.tsx`
- Create: `webapp/src/index.css`
- Create: `webapp/src/test-setup.ts`
- Test: `webapp/src/App.test.tsx`

**Interfaces:**
- Produces: a working `npm run dev` / `npm run build` / `npm test` pipeline, and a root
  `App` component (`src/App.tsx`) exporting `function App(): JSX.Element`, which Task 14
  replaces the body of.

- [ ] **Step 1: Scaffold the project**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
npm create vite@latest webapp -- --template react-ts
cd webapp
npm install
npm install react-router-dom @tanstack/react-query recharts qrcode
npm install -D tailwindcss postcss autoprefixer vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom @types/qrcode
npx tailwindcss init -p
```

- [ ] **Step 2: Configure Tailwind**

Replace `webapp/tailwind.config.js` content with:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
};
```

Replace `webapp/src/index.css` content with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 3: Configure the runtime config script**

Create `webapp/public/config.js` (the dev-time default; the production container
overwrites this file at startup — see Task 15):

```js
window.__APP_CONFIG__ = {
  apiBaseUrl: "http://localhost:8000",
};
```

Edit `webapp/index.html` to load it before the module script (add this line inside
`<head>`, before the existing `<script type="module" src="/src/main.tsx"></script>`):

```html
<script src="/config.js"></script>
```

- [ ] **Step 4: Configure Vitest**

Create `webapp/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
```

Create `webapp/src/test-setup.ts`:

```ts
import "@testing-library/jest-dom";
```

Add to `webapp/package.json`'s `"scripts"`:

```json
"test": "vitest run"
```

- [ ] **Step 5: Write the root App component and its test**

Replace `webapp/src/App.tsx`:

```tsx
export function App() {
  return <div className="p-8 text-xl">Dashboard placeholder — replaced in Task 14</div>;
}

export default App;
```

Create `webapp/src/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders without crashing", () => {
    render(<App />);
    expect(screen.getByText(/Dashboard placeholder/)).toBeInTheDocument();
  });
});
```

Replace `webapp/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 6: Run the test suite and the build**

Run: `cd webapp && npm test`
Expected: PASS (1 test).

Run: `cd webapp && npm run build`
Expected: builds successfully with no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/
git commit -m "Scaffold React/Vite/TypeScript/Tailwind webapp with Vitest"
```

---

## Task 2: Typed API client

**Files:**
- Create: `webapp/src/api/config.ts`
- Create: `webapp/src/api/client.ts`
- Test: `webapp/src/api/client.test.ts`

**Interfaces:**
- Produces: `getApiBaseUrl(): string`; `class ApiError extends Error { status: number; code: string; detail: unknown }`;
  `configureApiClient(tokenGetter: () => string | null, onUnauthorized: () => void): void`;
  `apiFetch<T>(path: string, options?: RequestInit): Promise<T>`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/api/client.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, configureApiClient } from "./client";

function mockFetchOnce(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    }),
  );
}

describe("apiFetch", () => {
  beforeEach(() => {
    configureApiClient(() => null, () => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the parsed JSON body on success", async () => {
    mockFetchOnce(200, { hello: "world" });
    const result = await apiFetch<{ hello: string }>("/api/v1/whatever");
    expect(result).toEqual({ hello: "world" });
  });

  it("returns undefined for a 204 response", async () => {
    mockFetchOnce(204, null);
    const result = await apiFetch<undefined>("/api/v1/whatever", { method: "DELETE" });
    expect(result).toBeUndefined();
  });

  it("throws ApiError with the structured error body on failure", async () => {
    mockFetchOnce(409, { code: "email_taken", message: "Already registered", detail: null });
    await expect(apiFetch("/api/v1/auth/register")).rejects.toMatchObject(
      new ApiError(409, "email_taken", "Already registered", null),
    );
  });

  it("attaches the Authorization header when a token is configured", async () => {
    configureApiClient(() => "the-token", () => {});
    mockFetchOnce(200, {});
    await apiFetch("/api/v1/sites");
    const [, requestInit] = vi.mocked(fetch).mock.calls[0];
    const headers = requestInit!.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer the-token");
  });

  it("calls onUnauthorized on a 401 without attaching a previous token forever", async () => {
    const onUnauthorized = vi.fn();
    configureApiClient(() => "stale-token", onUnauthorized);
    mockFetchOnce(401, { code: "invalid_token", message: "Invalid or expired token", detail: null });
    await expect(apiFetch("/api/v1/sites")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- client.test.ts`
Expected: FAIL with a module-not-found error.

- [ ] **Step 3: Implement**

Create `webapp/src/api/config.ts`:

```ts
interface AppConfig {
  apiBaseUrl: string;
}

declare global {
  interface Window {
    __APP_CONFIG__?: AppConfig;
  }
}

export function getApiBaseUrl(): string {
  return window.__APP_CONFIG__?.apiBaseUrl ?? "http://localhost:8000";
}
```

Create `webapp/src/api/client.ts`:

```ts
import { getApiBaseUrl } from "./config";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;

  constructor(status: number, code: string, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

interface ErrorBody {
  code?: string;
  message?: string;
  detail?: unknown;
}

type TokenGetter = () => string | null;
type UnauthorizedHandler = () => void;

let getToken: TokenGetter = () => null;
let onUnauthorized: UnauthorizedHandler = () => undefined;

export function configureApiClient(tokenGetter: TokenGetter, unauthorizedHandler: UnauthorizedHandler): void {
  getToken = tokenGetter;
  onUnauthorized = unauthorizedHandler;
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${getApiBaseUrl()}${path}`, { ...options, headers });

  if (response.status === 401) {
    onUnauthorized();
  }

  if (!response.ok) {
    const body: ErrorBody = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      body.code ?? "unknown_error",
      body.message ?? response.statusText,
      body.detail ?? null,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- client.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/api/
git commit -m "Add typed API client with central 401 handling"
```

---

## Task 3: Auth context and protected routes

**Files:**
- Create: `webapp/src/auth/AuthContext.tsx`
- Create: `webapp/src/auth/ProtectedRoute.tsx`
- Test: `webapp/src/auth/AuthContext.test.tsx`
- Test: `webapp/src/auth/ProtectedRoute.test.tsx`

**Interfaces:**
- Consumes: `configureApiClient` (Task 2).
- Produces: `AuthProvider({children}): JSX.Element`; `useAuth(): {accessToken: string | null; login(token: string): void; logout(): void}`;
  `ProtectedRoute(): JSX.Element` (a React Router layout route — renders `<Outlet/>` if
  authenticated, else `<Navigate to="/login"/>`).

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/auth/AuthContext.test.tsx`:

```tsx
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";

function Probe() {
  const { accessToken, login, logout } = useAuth();
  return (
    <div>
      <span data-testid="token">{accessToken ?? "none"}</span>
      <button onClick={() => login("new-token")}>login</button>
      <button onClick={() => logout()}>logout</button>
    </div>
  );
}

describe("AuthProvider", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("starts with no token when localStorage is empty", () => {
    render(<AuthProvider><Probe /></AuthProvider>);
    expect(screen.getByTestId("token")).toHaveTextContent("none");
  });

  it("login stores the token and updates context", async () => {
    render(<AuthProvider><Probe /></AuthProvider>);
    await act(async () => {
      await userEvent.click(screen.getByText("login"));
    });
    expect(screen.getByTestId("token")).toHaveTextContent("new-token");
    expect(localStorage.getItem("access_token")).toBe("new-token");
  });

  it("logout clears the token", async () => {
    localStorage.setItem("access_token", "existing-token");
    render(<AuthProvider><Probe /></AuthProvider>);
    expect(screen.getByTestId("token")).toHaveTextContent("existing-token");
    await act(async () => {
      await userEvent.click(screen.getByText("logout"));
    });
    expect(screen.getByTestId("token")).toHaveTextContent("none");
    expect(localStorage.getItem("access_token")).toBeNull();
  });
});
```

Create `webapp/src/auth/ProtectedRoute.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AuthProvider } from "./AuthContext";
import { ProtectedRoute } from "./ProtectedRoute";

function renderWithRoute(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<div>Login page</div>} />
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<div>Dashboard</div>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute", () => {
  it("redirects to /login when there is no token", () => {
    localStorage.clear();
    renderWithRoute("/");
    expect(screen.getByText("Login page")).toBeInTheDocument();
  });

  it("renders the nested route when a token is present", () => {
    localStorage.setItem("access_token", "a-token");
    renderWithRoute("/");
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    localStorage.clear();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- auth`
Expected: FAIL with module-not-found errors.

- [ ] **Step 3: Implement**

Create `webapp/src/auth/AuthContext.tsx`:

```tsx
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { configureApiClient } from "../api/client";

const STORAGE_KEY = "access_token";

interface AuthContextValue {
  accessToken: string | null;
  login: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [accessToken, setAccessToken] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY));

  const login = useCallback((token: string) => {
    localStorage.setItem(STORAGE_KEY, token);
    setAccessToken(token);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setAccessToken(null);
  }, []);

  useEffect(() => {
    configureApiClient(() => localStorage.getItem(STORAGE_KEY), logout);
  }, [logout]);

  const value = useMemo(() => ({ accessToken, login, logout }), [accessToken, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
```

Create `webapp/src/auth/ProtectedRoute.tsx`:

```tsx
import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function ProtectedRoute() {
  const { accessToken } = useAuth();
  if (!accessToken) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- auth`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/auth/
git commit -m "Add AuthContext and ProtectedRoute"
```

---

## Task 4: Register page

**Files:**
- Create: `webapp/src/pages/RegisterPage.tsx`
- Test: `webapp/src/pages/RegisterPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2).
- Produces: `RegisterPage(): JSX.Element` — on success, navigates to `/login`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/pages/RegisterPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { RegisterPage } from "./RegisterPage";

vi.mock("../api/client", () => ({ apiFetch: vi.fn() }));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/register"]}>
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RegisterPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("registers and navigates to /login on success", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ id: 1, email: "a@example.com" });
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("Email"), "a@example.com");
    await userEvent.type(screen.getByPlaceholderText("Password"), "a-password");
    await userEvent.type(screen.getByPlaceholderText("Confirm password"), "a-password");
    await userEvent.click(screen.getByText("Create account"));
    expect(await screen.findByText("Login page")).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/auth/register",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows an error and does not navigate when passwords don't match", async () => {
    renderPage();
    await userEvent.type(screen.getByPlaceholderText("Email"), "a@example.com");
    await userEvent.type(screen.getByPlaceholderText("Password"), "a-password");
    await userEvent.type(screen.getByPlaceholderText("Confirm password"), "different");
    await userEvent.click(screen.getByText("Create account"));
    expect(await screen.findByRole("alert")).toHaveTextContent("don't match");
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && npm test -- RegisterPage`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement**

Create `webapp/src/pages/RegisterPage.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";

export function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (password !== confirmPassword) {
      setError("Passwords don't match");
      return;
    }
    try {
      await apiFetch("/api/v1/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      navigate("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Create an account</h1>
      {error && <p role="alert" className="text-red-600">{error}</p>}
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email"
        className="w-full border p-2"
        required
      />
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Password"
        className="w-full border p-2"
        required
      />
      <input
        type="password"
        value={confirmPassword}
        onChange={(e) => setConfirmPassword(e.target.value)}
        placeholder="Confirm password"
        className="w-full border p-2"
        required
      />
      <button type="submit" className="w-full bg-blue-600 p-2 text-white">
        Create account
      </button>
      <p className="text-sm">
        <Link to="/login">Already have an account? Log in</Link>
      </p>
    </form>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- RegisterPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/pages/RegisterPage.tsx webapp/src/pages/RegisterPage.test.tsx
git commit -m "Add RegisterPage"
```

---

## Task 5: Login page

**Files:**
- Create: `webapp/src/pages/LoginPage.tsx`
- Test: `webapp/src/pages/LoginPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2).
- Produces: `LoginPage(): JSX.Element` — navigates to `/totp-setup` with
  `{state: {setupToken}}` or `/totp-verify` with `{state: {mfaToken}}` depending on the
  login response's `status`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/pages/LoginPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { LoginPage } from "./LoginPage";

vi.mock("../api/client", () => ({ apiFetch: vi.fn() }));

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="state">{JSON.stringify(location.state)}</div>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/totp-setup" element={<LocationProbe />} />
        <Route path="/totp-verify" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function submitLogin() {
  await userEvent.type(screen.getByPlaceholderText("Email"), "a@example.com");
  await userEvent.type(screen.getByPlaceholderText("Password"), "a-password");
  await userEvent.click(screen.getByText("Log in"));
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("navigates to /totp-setup with the setup_token when enrollment is required", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      status: "totp_setup_required",
      setup_token: "a-setup-token",
      mfa_token: null,
    });
    renderPage();
    await submitLogin();
    expect(await screen.findByTestId("state")).toHaveTextContent("a-setup-token");
  });

  it("navigates to /totp-verify with the mfa_token when already enrolled", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      status: "totp_required",
      setup_token: null,
      mfa_token: "an-mfa-token",
    });
    renderPage();
    await submitLogin();
    expect(await screen.findByTestId("state")).toHaveTextContent("an-mfa-token");
  });

  it("shows an error on invalid credentials and does not navigate", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(401, "invalid_credentials", "Email or password is incorrect", null));
    renderPage();
    await submitLogin();
    expect(await screen.findByRole("alert")).toHaveTextContent("incorrect");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && npm test -- LoginPage`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement**

Create `webapp/src/pages/LoginPage.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";

interface LoginChallenge {
  status: "totp_setup_required" | "totp_required";
  setup_token: string | null;
  mfa_token: string | null;
}

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await apiFetch<LoginChallenge>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      if (result.status === "totp_setup_required") {
        navigate("/totp-setup", { state: { setupToken: result.setup_token } });
      } else {
        navigate("/totp-verify", { state: { mfaToken: result.mfa_token } });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Log in</h1>
      {error && <p role="alert" className="text-red-600">{error}</p>}
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email"
        className="w-full border p-2"
        required
      />
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Password"
        className="w-full border p-2"
        required
      />
      <button type="submit" className="w-full bg-blue-600 p-2 text-white">
        Log in
      </button>
      <p className="flex justify-between text-sm">
        <Link to="/forgot-password">Forgot password?</Link>
        <Link to="/register">Create an account</Link>
      </p>
    </form>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- LoginPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/pages/LoginPage.tsx webapp/src/pages/LoginPage.test.tsx
git commit -m "Add LoginPage"
```

---

## Task 6: TOTP setup page

**Files:**
- Create: `webapp/src/pages/TotpSetupPage.tsx`
- Test: `webapp/src/pages/TotpSetupPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2), `useAuth` (Task 3), `qrcode`.
- Produces: `TotpSetupPage(): JSX.Element` — reads `location.state.setupToken`; on
  confirm, logs in via `useAuth().login` and navigates to `/` only after the user
  acknowledges the shown recovery codes.

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/pages/TotpSetupPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { TotpSetupPage } from "./TotpSetupPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderWithState(state: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/totp-setup", state }]}>
      <AuthProvider>
        <Routes>
          <Route path="/totp-setup" element={<TotpSetupPage />} />
          <Route path="/" element={<div>Dashboard</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("TotpSetupPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
  });

  it("shows a re-login message when there is no setup token in router state", () => {
    renderWithState(null);
    expect(screen.getByRole("alert")).toHaveTextContent("log in again");
  });

  it("fetches the secret on mount and shows a QR code", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      secret: "JBSWY3DPEHPK3PXP",
      otpauth_uri: "otpauth://totp/Enphase%20Data:a@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Enphase%20Data",
    });
    renderWithState({ setupToken: "a-setup-token" });
    expect(await screen.findByAltText("TOTP QR code")).toBeInTheDocument();
    expect(screen.getByText(/JBSWY3DPEHPK3PXP/)).toBeInTheDocument();
  });

  it("shows recovery codes after a correct confirm code, then continues to / once acknowledged", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/x" })
      .mockResolvedValueOnce({
        access_token: "final-access-token",
        token_type: "bearer",
        recovery_codes: Array.from({ length: 10 }, (_, i) => `code${i}`),
      });
    renderWithState({ setupToken: "a-setup-token" });
    await screen.findByAltText("TOTP QR code");
    await userEvent.type(screen.getByPlaceholderText("6-digit code"), "123456");
    await userEvent.click(screen.getByText("Verify"));
    expect(await screen.findByText("code0")).toBeInTheDocument();

    const checkbox = screen.getByRole("checkbox");
    await userEvent.click(checkbox);
    await userEvent.click(screen.getByText("Continue"));
    expect(await screen.findByText("Dashboard")).toBeInTheDocument();
    expect(localStorage.getItem("access_token")).toBe("final-access-token");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- TotpSetupPage`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement**

Create `webapp/src/pages/TotpSetupPage.tsx`:

```tsx
import QRCode from "qrcode";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";

interface LocationState {
  setupToken: string;
}

interface SetupStartResponse {
  secret: string;
  otpauth_uri: string;
}

interface SetupConfirmResponse {
  access_token: string;
  token_type: string;
  recovery_codes: string[];
}

export function TotpSetupPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { login } = useAuth();
  const setupToken = (location.state as LocationState | null)?.setupToken ?? null;

  const [secret, setSecret] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingAccessToken = useRef<string | null>(null);

  useEffect(() => {
    if (!setupToken) return;
    apiFetch<SetupStartResponse>("/api/v1/auth/totp/setup/start", {
      method: "POST",
      headers: { Authorization: `Bearer ${setupToken}` },
    }).then(async (result) => {
      setSecret(result.secret);
      setQrDataUrl(await QRCode.toDataURL(result.otpauth_uri));
    });
  }, [setupToken]);

  if (!setupToken) {
    return <p role="alert">Your session expired. Please log in again.</p>;
  }

  async function handleConfirm(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await apiFetch<SetupConfirmResponse>("/api/v1/auth/totp/setup/confirm", {
        method: "POST",
        headers: { Authorization: `Bearer ${setupToken}` },
        body: JSON.stringify({ code }),
      });
      pendingAccessToken.current = result.access_token;
      setRecoveryCodes(result.recovery_codes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Incorrect code");
    }
  }

  function handleContinue() {
    if (pendingAccessToken.current) {
      login(pendingAccessToken.current);
      navigate("/");
    }
  }

  if (recoveryCodes) {
    return (
      <div className="mx-auto mt-24 max-w-md space-y-4">
        <h1 className="text-xl font-semibold">Save your recovery codes</h1>
        <p>Store these somewhere safe. Each works once if you lose your authenticator device.</p>
        <ul className="bg-gray-100 p-4 font-mono">
          {recoveryCodes.map((rc) => (
            <li key={rc}>{rc}</li>
          ))}
        </ul>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
          I've saved these codes
        </label>
        <button
          disabled={!acknowledged}
          onClick={handleContinue}
          className="bg-blue-600 p-2 text-white disabled:opacity-50"
        >
          Continue
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Set up two-factor authentication</h1>
      {qrDataUrl && <img src={qrDataUrl} alt="TOTP QR code" />}
      {secret && <p className="font-mono text-sm">Manual entry key: {secret}</p>}
      <form onSubmit={handleConfirm} className="space-y-4">
        {error && <p role="alert" className="text-red-600">{error}</p>}
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="6-digit code"
          className="w-full border p-2"
          required
        />
        <button type="submit" className="w-full bg-blue-600 p-2 text-white">
          Verify
        </button>
      </form>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- TotpSetupPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/pages/TotpSetupPage.tsx webapp/src/pages/TotpSetupPage.test.tsx
git commit -m "Add TotpSetupPage"
```

---

## Task 7: TOTP verify page

**Files:**
- Create: `webapp/src/pages/TotpVerifyPage.tsx`
- Test: `webapp/src/pages/TotpVerifyPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2), `useAuth` (Task 3).
- Produces: `TotpVerifyPage(): JSX.Element` — reads `location.state.mfaToken`; on
  success, logs in and navigates to `/`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/pages/TotpVerifyPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { TotpVerifyPage } from "./TotpVerifyPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderWithState(state: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/totp-verify", state }]}>
      <AuthProvider>
        <Routes>
          <Route path="/totp-verify" element={<TotpVerifyPage />} />
          <Route path="/" element={<div>Dashboard</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("TotpVerifyPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
  });

  it("shows a re-login message when there is no mfa token in router state", () => {
    renderWithState(null);
    expect(screen.getByRole("alert")).toHaveTextContent("log in again");
  });

  it("logs in and navigates to / on a correct code", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ access_token: "final-token", token_type: "bearer" });
    renderWithState({ mfaToken: "an-mfa-token" });
    await userEvent.type(screen.getByPlaceholderText("6-digit code"), "123456");
    await userEvent.click(screen.getByText("Verify"));
    expect(await screen.findByText("Dashboard")).toBeInTheDocument();
    expect(localStorage.getItem("access_token")).toBe("final-token");
  });

  it("toggles to a recovery-code placeholder", async () => {
    renderWithState({ mfaToken: "an-mfa-token" });
    await userEvent.click(screen.getByText("Use a recovery code instead"));
    expect(screen.getByPlaceholderText("Recovery code")).toBeInTheDocument();
  });

  it("shows an error on an incorrect code", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(401, "invalid_code", "Incorrect code", null));
    renderWithState({ mfaToken: "an-mfa-token" });
    await userEvent.type(screen.getByPlaceholderText("6-digit code"), "000000");
    await userEvent.click(screen.getByText("Verify"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect code");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- TotpVerifyPage`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement**

Create `webapp/src/pages/TotpVerifyPage.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { useAuth } from "../auth/AuthContext";

interface LocationState {
  mfaToken: string;
}

interface VerifyResponse {
  access_token: string;
  token_type: string;
}

export function TotpVerifyPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { login } = useAuth();
  const mfaToken = (location.state as LocationState | null)?.mfaToken ?? null;

  const [code, setCode] = useState("");
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!mfaToken) {
    return <p role="alert">Your session expired. Please log in again.</p>;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await apiFetch<VerifyResponse>("/api/v1/auth/totp/verify", {
        method: "POST",
        headers: { Authorization: `Bearer ${mfaToken}` },
        body: JSON.stringify({ code }),
      });
      login(result.access_token);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Incorrect code");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Enter your {useRecoveryCode ? "recovery" : "authenticator"} code</h1>
      {error && <p role="alert" className="text-red-600">{error}</p>}
      <input
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder={useRecoveryCode ? "Recovery code" : "6-digit code"}
        className="w-full border p-2"
        required
      />
      <button type="submit" className="w-full bg-blue-600 p-2 text-white">
        Verify
      </button>
      <button type="button" onClick={() => setUseRecoveryCode((v) => !v)} className="text-sm underline">
        {useRecoveryCode ? "Use authenticator code instead" : "Use a recovery code instead"}
      </button>
    </form>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- TotpVerifyPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/pages/TotpVerifyPage.tsx webapp/src/pages/TotpVerifyPage.test.tsx
git commit -m "Add TotpVerifyPage"
```

---

## Task 8: Forgot-password and reset-password pages

**Files:**
- Create: `webapp/src/pages/ForgotPasswordPage.tsx`
- Create: `webapp/src/pages/ResetPasswordPage.tsx`
- Test: `webapp/src/pages/ForgotPasswordPage.test.tsx`
- Test: `webapp/src/pages/ResetPasswordPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2).
- Produces: `ForgotPasswordPage(): JSX.Element`; `ResetPasswordPage(): JSX.Element`
  (reads `?token=` from the URL via `useSearchParams`).

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/pages/ForgotPasswordPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ForgotPasswordPage } from "./ForgotPasswordPage";

vi.mock("../api/client", () => ({ apiFetch: vi.fn() }));

describe("ForgotPasswordPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows the generic confirmation message after submit, regardless of outcome", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ message: "If that email is registered, a password reset link has been sent." });
    render(
      <MemoryRouter>
        <ForgotPasswordPage />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByPlaceholderText("Email"), "a@example.com");
    await userEvent.click(screen.getByText("Send reset link"));
    expect(await screen.findByText(/password reset link has been sent/)).toBeInTheDocument();
  });
});
```

Create `webapp/src/pages/ResetPasswordPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { ResetPasswordPage } from "./ResetPasswordPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/reset-password${search}`]}>
      <Routes>
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ResetPasswordPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("submits the token from the URL and the new password, then navigates to /login", async () => {
    vi.mocked(apiFetch).mockResolvedValue(undefined);
    renderPage("?token=abc123");
    await userEvent.type(screen.getByPlaceholderText("New password"), "a-new-password");
    await userEvent.click(screen.getByText("Reset password"));
    expect(await screen.findByText("Login page")).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/auth/password-reset/confirm",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ token: "abc123", new_password: "a-new-password" }),
      }),
    );
  });

  it("shows an error for an invalid or expired token", async () => {
    const { ApiError } = await import("../api/client");
    vi.mocked(apiFetch).mockRejectedValue(new ApiError(400, "invalid_token", "This reset link is invalid or has expired", null));
    renderPage("?token=expired");
    await userEvent.type(screen.getByPlaceholderText("New password"), "a-new-password");
    await userEvent.click(screen.getByText("Reset password"));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- ForgotPasswordPage ResetPasswordPage`
Expected: FAIL with module-not-found errors.

- [ ] **Step 3: Implement**

Create `webapp/src/pages/ForgotPasswordPage.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { apiFetch } from "../api/client";

interface RequestResponse {
  message: string;
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const result = await apiFetch<RequestResponse>("/api/v1/auth/password-reset/request", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
    setMessage(result.message);
  }

  if (message) {
    return <p className="mx-auto mt-24 max-w-sm">{message}</p>;
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Forgot your password?</h1>
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email"
        className="w-full border p-2"
        required
      />
      <button type="submit" className="w-full bg-blue-600 p-2 text-white">
        Send reset link
      </button>
    </form>
  );
}
```

Create `webapp/src/pages/ResetPasswordPage.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";

export function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await apiFetch("/api/v1/auth/password-reset/confirm", {
        method: "POST",
        body: JSON.stringify({ token, new_password: newPassword }),
      });
      navigate("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "This reset link is invalid or has expired");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Set a new password</h1>
      {error && <p role="alert" className="text-red-600">{error}</p>}
      <input
        type="password"
        value={newPassword}
        onChange={(e) => setNewPassword(e.target.value)}
        placeholder="New password"
        className="w-full border p-2"
        required
      />
      <button type="submit" className="w-full bg-blue-600 p-2 text-white">
        Reset password
      </button>
    </form>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- ForgotPasswordPage ResetPasswordPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/pages/ForgotPasswordPage.tsx webapp/src/pages/ResetPasswordPage.tsx webapp/src/pages/ForgotPasswordPage.test.tsx webapp/src/pages/ResetPasswordPage.test.tsx
git commit -m "Add ForgotPasswordPage and ResetPasswordPage"
```

---

## Task 9: Site data hooks and the site picker

**Files:**
- Create: `webapp/src/sites/useSites.ts`
- Create: `webapp/src/pages/SitePickerPage.tsx`
- Test: `webapp/src/pages/SitePickerPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2), TanStack Query.
- Produces: `Site { id: number; owner_user_id: number; name: string; timezone: string }`;
  `useSites(): UseQueryResult<Site[]>`; `useCreateSite(): UseMutationResult<Site, Error, {name: string; timezone: string}>`;
  `SitePickerPage(): JSX.Element` — 0 sites → onboarding form; 1 site → redirects to
  `/sites/:id`; 2+ → a switcher list linking to each `/sites/:id`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/pages/SitePickerPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { SitePickerPage } from "./SitePickerPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites"]}>
        <Routes>
          <Route path="/sites" element={<SitePickerPage />} />
          <Route path="/sites/:siteId" element={<div>Dashboard for site</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SitePickerPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows an onboarding form when the user has no sites", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findByText("Create your first site")).toBeInTheDocument();
  });

  it("creates a site from the onboarding form", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ id: 1, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney" });
    renderPage();
    await screen.findByText("Create your first site");
    await userEvent.type(screen.getByPlaceholderText("Site name"), "Home");
    await userEvent.type(screen.getByPlaceholderText("Timezone (e.g. Australia/Sydney)"), "Australia/Sydney");
    await userEvent.click(screen.getByText("Create site"));
    expect(await screen.findByText("Dashboard for site")).toBeInTheDocument();
  });

  it("redirects straight through when the user has exactly one site", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney" },
    ]);
    renderPage();
    expect(await screen.findByText("Dashboard for site")).toBeInTheDocument();
  });

  it("shows a switcher when the user has multiple sites", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      { id: 1, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney" },
      { id: 2, owner_user_id: 1, name: "Cabin", timezone: "Australia/Hobart" },
    ]);
    renderPage();
    expect(await screen.findByText("Home")).toBeInTheDocument();
    expect(screen.getByText("Cabin")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- SitePickerPage`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement**

Create `webapp/src/sites/useSites.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface Site {
  id: number;
  owner_user_id: number;
  name: string;
  timezone: string;
}

interface CreateSiteInput {
  name: string;
  timezone: string;
}

export function useSites() {
  return useQuery({
    queryKey: ["sites"],
    queryFn: () => apiFetch<Site[]>("/api/v1/sites"),
  });
}

export function useCreateSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSiteInput) =>
      apiFetch<Site>("/api/v1/sites", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sites"] });
    },
  });
}
```

Create `webapp/src/pages/SitePickerPage.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useCreateSite, useSites } from "../sites/useSites";

function OnboardingForm() {
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("");
  const createSite = useCreateSite();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createSite.mutate({ name, timezone });
  }

  if (createSite.isSuccess) {
    return <Navigate to={`/sites/${createSite.data.id}`} replace />;
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto mt-24 max-w-sm space-y-4">
      <h1 className="text-xl font-semibold">Create your first site</h1>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Site name"
        className="w-full border p-2"
        required
      />
      <input
        value={timezone}
        onChange={(e) => setTimezone(e.target.value)}
        placeholder="Timezone (e.g. Australia/Sydney)"
        className="w-full border p-2"
        required
      />
      <button type="submit" className="w-full bg-blue-600 p-2 text-white">
        Create site
      </button>
    </form>
  );
}

export function SitePickerPage() {
  const { data: sites, isLoading } = useSites();

  if (isLoading) {
    return <p className="mx-auto mt-24 max-w-sm">Loading your sites...</p>;
  }
  if (!sites || sites.length === 0) {
    return <OnboardingForm />;
  }
  if (sites.length === 1) {
    return <Navigate to={`/sites/${sites[0].id}`} replace />;
  }
  return (
    <div className="mx-auto mt-24 max-w-sm space-y-2">
      <h1 className="text-xl font-semibold">Choose a site</h1>
      <ul className="space-y-2">
        {sites.map((site) => (
          <li key={site.id}>
            <Link to={`/sites/${site.id}`} className="block border p-2 hover:bg-gray-50">
              {site.name}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- SitePickerPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/sites/ webapp/src/pages/SitePickerPage.tsx webapp/src/pages/SitePickerPage.test.tsx
git commit -m "Add site data hooks and SitePickerPage"
```

---

## Task 10: Dashboard — live tiles

**Files:**
- Create: `webapp/src/dashboard/useLiveReadings.ts`
- Create: `webapp/src/pages/DashboardPage.tsx`
- Test: `webapp/src/pages/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2), `Site` (Task 9).
- Produces: `Reading { ts: string; metric: string; value: number }`;
  `useLatestEnergyReadings(siteId: number): UseQueryResult<Reading[]>` (polls every 60s);
  `DashboardPage(): JSX.Element` (reads `siteId` from the route's `:siteId` param).

- [ ] **Step 1: Write the failing test**

Create `webapp/src/pages/DashboardPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { DashboardPage } from "./DashboardPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7"]}>
        <Routes>
          <Route path="/sites/:siteId" element={<DashboardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DashboardPage live tiles", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows the latest value for each known energy metric", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path.includes("/readings/energy")) {
        return Promise.resolve([
          { ts: "2026-10-01T00:00:00Z", metric: "production_w", value: 2500 },
          { ts: "2026-10-01T00:00:00Z", metric: "consumption_w", value: 1200 },
        ]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    expect(await screen.findByText("2500 W")).toBeInTheDocument();
    expect(await screen.findByText("1200 W")).toBeInTheDocument();
  });

  it("requested the readings for the site id from the route", async () => {
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderPage();
    await screen.findByText(/Production/);
    const calledPaths = vi.mocked(apiFetch).mock.calls.map(([path]) => path as string);
    expect(calledPaths.some((p) => p.includes("/sites/7/readings/energy"))).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && npm test -- DashboardPage`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement**

Create `webapp/src/dashboard/useLiveReadings.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface Reading {
  ts: string;
  metric: string;
  value: number;
}

const LIVE_POLL_MS = 60_000;

export function useLatestEnergyReadings(siteId: number) {
  return useQuery({
    queryKey: ["energy-readings", siteId, "latest"],
    queryFn: () => {
      const to = new Date();
      const from = new Date(to.getTime() - 5 * 60_000);
      const params = new URLSearchParams({ from: from.toISOString(), to: to.toISOString() });
      return apiFetch<Reading[]>(`/api/v1/sites/${siteId}/readings/energy?${params}`);
    },
    refetchInterval: LIVE_POLL_MS,
  });
}
```

Create `webapp/src/pages/DashboardPage.tsx`:

```tsx
import { useParams } from "react-router-dom";
import { Reading, useLatestEnergyReadings } from "../dashboard/useLiveReadings";

const TILE_LABELS: Record<string, string> = {
  production_w: "Production",
  consumption_w: "Consumption",
  grid_import_w: "Grid import",
  grid_export_w: "Grid export",
  battery_charge_w: "Battery charge",
  battery_discharge_w: "Battery discharge",
  battery_soc_pct: "Battery",
};

function latestByMetric(readings: Reading[]): Map<string, Reading> {
  const byMetric = new Map<string, Reading>();
  for (const reading of readings) {
    const existing = byMetric.get(reading.metric);
    if (!existing || reading.ts > existing.ts) {
      byMetric.set(reading.metric, reading);
    }
  }
  return byMetric;
}

function LiveTiles({ siteId }: { siteId: number }) {
  const { data: readings } = useLatestEnergyReadings(siteId);
  const latest = latestByMetric(readings ?? []);

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
      {Array.from(latest.entries()).map(([metric, reading]) => (
        <div key={metric} className="border p-4">
          <p className="text-sm text-gray-500">{TILE_LABELS[metric] ?? metric}</p>
          <p className="text-2xl font-semibold">
            {reading.value} {metric === "battery_soc_pct" ? "%" : "W"}
          </p>
        </div>
      ))}
    </div>
  );
}

export function DashboardPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const parsedSiteId = Number(siteId);

  return (
    <div className="mx-auto mt-8 max-w-4xl space-y-6 px-4">
      <h1 className="text-xl font-semibold">Dashboard</h1>
      <LiveTiles siteId={parsedSiteId} />
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- DashboardPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/dashboard/useLiveReadings.ts webapp/src/pages/DashboardPage.tsx webapp/src/pages/DashboardPage.test.tsx
git commit -m "Add dashboard live tiles"
```

---

## Task 11: Dashboard — historical chart, net/gross toggle, cost

**Files:**
- Create: `webapp/src/dashboard/useHistoricalData.ts`
- Modify: `webapp/src/pages/DashboardPage.tsx`
- Modify: `webapp/src/pages/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2), `Reading` (Task 10).
- Produces: `CostSummary { total_cost_c: number; total_import_kwh: number; total_export_kwh: number; minutes_missing_price: number }`;
  `useHistoricalEnergyReadings(siteId: number, range: {from: Date; to: Date}): UseQueryResult<Reading[]>`;
  `useCost(siteId: number, range: {from: Date; to: Date}, method: "net" | "gross"): UseQueryResult<CostSummary>`.

- [ ] **Step 1: Write the failing test**

Append to `webapp/src/pages/DashboardPage.test.tsx`:

```tsx
describe("DashboardPage historical chart and cost", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("shows the computed cost for the selected net/gross method and re-fetches when toggled", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path.includes("/cost")) {
        const method = path.includes("method=net") ? "net" : "gross";
        return Promise.resolve({
          total_cost_c: method === "net" ? 150 : 300,
          total_import_kwh: 10,
          total_export_kwh: 4,
          minutes_missing_price: 0,
        });
      }
      return Promise.resolve([]);
    });
    renderPage();
    expect(await screen.findByText("$1.50")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Gross"));
    expect(await screen.findByText("$3.00")).toBeInTheDocument();
  });
});
```

Add the needed `userEvent` import to the top of `webapp/src/pages/DashboardPage.test.tsx`:

```tsx
import userEvent from "@testing-library/user-event";
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && npm test -- DashboardPage`
Expected: FAIL — no cost display or toggle exists yet.

- [ ] **Step 3: Implement**

Create `webapp/src/dashboard/useHistoricalData.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Reading } from "./useLiveReadings";

export interface CostSummary {
  total_cost_c: number;
  total_import_kwh: number;
  total_export_kwh: number;
  minutes_missing_price: number;
}

export interface DateRange {
  from: Date;
  to: Date;
}

export function useHistoricalEnergyReadings(siteId: number, range: DateRange) {
  const params = new URLSearchParams({ from: range.from.toISOString(), to: range.to.toISOString() });
  return useQuery({
    queryKey: ["energy-readings", siteId, "history", range.from.toISOString(), range.to.toISOString()],
    queryFn: () => apiFetch<Reading[]>(`/api/v1/sites/${siteId}/readings/energy?${params}`),
  });
}

export function useCost(siteId: number, range: DateRange, method: "net" | "gross") {
  const params = new URLSearchParams({
    from: range.from.toISOString(),
    to: range.to.toISOString(),
    method,
  });
  return useQuery({
    queryKey: ["cost", siteId, range.from.toISOString(), range.to.toISOString(), method],
    queryFn: () => apiFetch<CostSummary>(`/api/v1/sites/${siteId}/cost?${params}`),
  });
}
```

Replace `webapp/src/pages/DashboardPage.tsx` with (adds the chart + toggle around the
existing `LiveTiles`):

```tsx
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useHistoricalEnergyReadings, useCost } from "../dashboard/useHistoricalData";
import { Reading, useLatestEnergyReadings } from "../dashboard/useLiveReadings";

const TILE_LABELS: Record<string, string> = {
  production_w: "Production",
  consumption_w: "Consumption",
  grid_import_w: "Grid import",
  grid_export_w: "Grid export",
  battery_charge_w: "Battery charge",
  battery_discharge_w: "Battery discharge",
  battery_soc_pct: "Battery",
};

function latestByMetric(readings: Reading[]): Map<string, Reading> {
  const byMetric = new Map<string, Reading>();
  for (const reading of readings) {
    const existing = byMetric.get(reading.metric);
    if (!existing || reading.ts > existing.ts) {
      byMetric.set(reading.metric, reading);
    }
  }
  return byMetric;
}

function LiveTiles({ siteId }: { siteId: number }) {
  const { data: readings } = useLatestEnergyReadings(siteId);
  const latest = latestByMetric(readings ?? []);

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
      {Array.from(latest.entries()).map(([metric, reading]) => (
        <div key={metric} className="border p-4">
          <p className="text-sm text-gray-500">{TILE_LABELS[metric] ?? metric}</p>
          <p className="text-2xl font-semibold">
            {reading.value} {metric === "battery_soc_pct" ? "%" : "W"}
          </p>
        </div>
      ))}
    </div>
  );
}

function rangeForDays(days: number) {
  const to = new Date();
  const from = new Date(to.getTime() - days * 24 * 60 * 60_000);
  return { from, to };
}

function HistoryAndCost({ siteId }: { siteId: number }) {
  const [days, setDays] = useState(1);
  const [method, setMethod] = useState<"net" | "gross">("net");
  const range = rangeForDays(days);
  const { data: history } = useHistoricalEnergyReadings(siteId, range);
  const { data: cost } = useCost(siteId, range, method);

  const chartData = (history ?? [])
    .filter((r) => r.metric === "production_w")
    .map((r) => ({ ts: r.ts, value: r.value }));

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="border p-2">
          <option value={1}>Today</option>
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
        </select>
        <label className="flex items-center gap-1">
          <input
            type="radio"
            name="method"
            checked={method === "net"}
            onChange={() => setMethod("net")}
            aria-label="Net"
          />
          Net
        </label>
        <label className="flex items-center gap-1">
          <input
            type="radio"
            name="method"
            checked={method === "gross"}
            onChange={() => setMethod("gross")}
            aria-label="Gross"
          />
          Gross
        </label>
      </div>
      {cost && <p className="text-lg">Cost: ${(cost.total_cost_c / 100).toFixed(2)}</p>}
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={chartData}>
          <XAxis dataKey="ts" hide />
          <YAxis />
          <Tooltip />
          <Line type="monotone" dataKey="value" stroke="#2563eb" dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function DashboardPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const parsedSiteId = Number(siteId);

  return (
    <div className="mx-auto mt-8 max-w-4xl space-y-6 px-4">
      <h1 className="text-xl font-semibold">Dashboard</h1>
      <LiveTiles siteId={parsedSiteId} />
      <HistoryAndCost siteId={parsedSiteId} />
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- DashboardPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/dashboard/useHistoricalData.ts webapp/src/pages/DashboardPage.tsx webapp/src/pages/DashboardPage.test.tsx
git commit -m "Add historical chart, net/gross toggle, and cost to the dashboard"
```

---

## Task 12: Sources management page

**Files:**
- Create: `webapp/src/sources/useSources.ts`
- Create: `webapp/src/pages/SourcesPage.tsx`
- Test: `webapp/src/pages/SourcesPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2).
- Produces: `Source { id: number; site_id: number; kind: string; provider: string; enabled: boolean }`;
  `useSources(siteId: number): UseQueryResult<Source[]>`;
  `useCreateSource(siteId: number): UseMutationResult<SourceCreateResult, Error, {kind: string; provider: string}>`
  where `SourceCreateResult` adds `collector_api_key: string`; `SourcesPage(): JSX.Element`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/pages/SourcesPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { SourcesPage } from "./SourcesPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7/sources"]}>
        <Routes>
          <Route path="/sites/:siteId/sources" element={<SourcesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SourcesPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("lists existing sources", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce([
      { id: 1, site_id: 7, kind: "energy", provider: "envoy", enabled: true },
    ]);
    renderPage();
    expect(await screen.findByText("envoy")).toBeInTheDocument();
  });

  it("creates a source and shows the generated collector key once", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({
        id: 2,
        site_id: 7,
        kind: "weather",
        provider: "weatherlink_live",
        enabled: true,
        collector_api_key: "raw-generated-key",
      });
    renderPage();
    await screen.findByText("No sources yet.");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "weather");
    await userEvent.type(screen.getByPlaceholderText("Provider"), "weatherlink_live");
    await userEvent.click(screen.getByText("Create source"));
    expect(await screen.findByText("raw-generated-key")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- SourcesPage`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement**

Create `webapp/src/sources/useSources.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface Source {
  id: number;
  site_id: number;
  kind: string;
  provider: string;
  enabled: boolean;
}

export interface SourceCreateResult extends Source {
  collector_api_key: string;
}

interface CreateSourceInput {
  kind: string;
  provider: string;
}

export function useSources(siteId: number) {
  return useQuery({
    queryKey: ["sources", siteId],
    queryFn: () => apiFetch<Source[]>(`/api/v1/sites/${siteId}/sources`),
  });
}

export function useCreateSource(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSourceInput) =>
      apiFetch<SourceCreateResult>(`/api/v1/sites/${siteId}/sources`, {
        method: "POST",
        body: JSON.stringify({ ...input, config: {} }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources", siteId] });
    },
  });
}
```

Create `webapp/src/pages/SourcesPage.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { useParams } from "react-router-dom";
import { useCreateSource, useSources } from "../sources/useSources";

function CreateSourceForm({ siteId }: { siteId: number }) {
  const [kind, setKind] = useState("energy");
  const [provider, setProvider] = useState("");
  const createSource = useCreateSource(siteId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createSource.mutate({ kind, provider });
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <label className="flex flex-col text-sm">
          Kind
          <select
            aria-label="Kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            className="border p-2"
          >
            <option value="energy">energy</option>
            <option value="pricing">pricing</option>
            <option value="weather">weather</option>
          </select>
        </label>
        <input
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          placeholder="Provider"
          className="border p-2"
          required
        />
        <button type="submit" className="bg-blue-600 p-2 text-white">
          Create source
        </button>
      </form>
      {createSource.data && (
        <p className="border bg-yellow-50 p-4">
          Collector key (shown once — copy it into the collector's config now):{" "}
          <span className="font-mono">{createSource.data.collector_api_key}</span>
        </p>
      )}
    </div>
  );
}

export function SourcesPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const parsedSiteId = Number(siteId);
  const { data: sources } = useSources(parsedSiteId);

  return (
    <div className="mx-auto mt-8 max-w-2xl space-y-6 px-4">
      <h1 className="text-xl font-semibold">Sources</h1>
      {sources && sources.length === 0 && <p>No sources yet.</p>}
      <ul className="space-y-2">
        {(sources ?? []).map((source) => (
          <li key={source.id} className="flex justify-between border p-2">
            <span>
              {source.kind} — {source.provider}
            </span>
            <span>{source.enabled ? "enabled" : "disabled"}</span>
          </li>
        ))}
      </ul>
      <CreateSourceForm siteId={parsedSiteId} />
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- SourcesPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/sources/ webapp/src/pages/SourcesPage.tsx webapp/src/pages/SourcesPage.test.tsx
git commit -m "Add SourcesPage"
```

---

## Task 13: Billing management page

**Files:**
- Create: `webapp/src/billing/useBilling.ts`
- Create: `webapp/src/pages/BillingPage.tsx`
- Test: `webapp/src/pages/BillingPage.test.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 2). Confirmed against `core-api/core_api/routers/billing.py`:
  the router's prefix is `/sites/{site_id}`, so tariffs are at `/sites/{id}/tariffs`,
  demand windows at `/sites/{id}/tariffs/{tariff_id}/demand-windows`, and billing cycles
  at `/sites/{id}/billing-cycle`.
- Produces: `SiteTariff { id: number; site_id: number; effective_from: string; effective_to: string | null; service_availability_c_per_day: number }`;
  `DemandWindow { id: number; site_tariff_id: number; label: string; months: number[]; days_of_week: number[]; start_time: string; end_time: string; rate_c_per_kwh: number; measurement_interval_minutes: number }`;
  `BillingCycle { id: number; site_id: number; effective_from: string; effective_to: string | null; cycle_months: number; anchor_day: number }`;
  `DemandCharge { id: number; billing_period_start: string; billing_period_end: string; charge_amount: number }`;
  `useTariffs(siteId)`, `useCreateTariff(siteId)`, `useEndTariff(siteId)` (PATCHes
  `effective_to` to close out a tariff — rates are effective-dated, not edited in place,
  per the Core API spec); `useDemandWindows(tariffId)`, `useCreateDemandWindow(siteId, tariffId)`;
  `useBillingCycles(siteId)`, `useCreateBillingCycle(siteId)`, `useEndBillingCycle(siteId)`;
  `useDemandCharges(siteId)`; `BillingPage(): JSX.Element`.

- [ ] **Step 1: Write the failing tests**

Create `webapp/src/pages/BillingPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { BillingPage } from "./BillingPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sites/7/billing"]}>
        <Routes>
          <Route path="/sites/:siteId/billing" element={<BillingPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function emptyListsFetch(_path: string) {
  return Promise.resolve([]);
}

describe("BillingPage", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("lists existing tariffs and creates a new one", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();
    await screen.findByText("No tariffs yet.");
    await userEvent.type(screen.getByPlaceholderText("Service availability (cents/day)"), "95");
    await userEvent.click(screen.getByText("Add tariff"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/tariffs",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("creates a demand window under an existing tariff", async () => {
    vi.mocked(apiFetch).mockImplementation((path: string) => {
      if (path === "/api/v1/sites/7/tariffs") {
        return Promise.resolve([
          { id: 1, site_id: 7, effective_from: "2026-01-01T00:00:00Z", effective_to: null, service_availability_c_per_day: 95 },
        ]);
      }
      return Promise.resolve([]);
    });
    renderPage();
    await screen.findByText(/95c\/day/);
    await userEvent.type(screen.getByPlaceholderText("Window label"), "Summer peak");
    await userEvent.type(screen.getByPlaceholderText("Months (e.g. 12,1,2)"), "12,1,2");
    await userEvent.type(screen.getByPlaceholderText("Days of week (0=Mon .. 6=Sun)"), "0,1,2,3,4");
    await userEvent.type(screen.getByPlaceholderText("Start time (HH:MM)"), "16:00");
    await userEvent.type(screen.getByPlaceholderText("End time (HH:MM)"), "20:00");
    await userEvent.type(screen.getByPlaceholderText("Rate (cents/kWh)"), "45");
    await userEvent.click(screen.getByText("Add demand window"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/tariffs/1/demand-windows",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("lists existing billing cycles and creates a new one", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();
    await screen.findByText("No billing cycles yet.");
    await userEvent.type(screen.getByPlaceholderText("Cycle length (months)"), "3");
    await userEvent.type(screen.getByPlaceholderText("Anchor day (1-31)"), "1");
    await userEvent.click(screen.getByText("Add billing cycle"));
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/sites/7/billing-cycle",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows an explicit empty state for demand charges, not an error", async () => {
    vi.mocked(apiFetch).mockImplementation(emptyListsFetch);
    renderPage();
    expect(await screen.findByText(/No demand charges yet/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- BillingPage`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement**

Create `webapp/src/billing/useBilling.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface SiteTariff {
  id: number;
  site_id: number;
  effective_from: string;
  effective_to: string | null;
  service_availability_c_per_day: number;
}

export interface DemandWindow {
  id: number;
  site_tariff_id: number;
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  rate_c_per_kwh: number;
  measurement_interval_minutes: number;
}

export interface BillingCycle {
  id: number;
  site_id: number;
  effective_from: string;
  effective_to: string | null;
  cycle_months: number;
  anchor_day: number;
}

export interface DemandCharge {
  id: number;
  billing_period_start: string;
  billing_period_end: string;
  charge_amount: number;
}

interface CreateTariffInput {
  effective_from: string;
  service_availability_c_per_day: number;
}

interface CreateDemandWindowInput {
  label: string;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  rate_c_per_kwh: number;
}

interface CreateBillingCycleInput {
  effective_from: string;
  cycle_months: number;
  anchor_day: number;
}

export function useTariffs(siteId: number) {
  return useQuery({
    queryKey: ["tariffs", siteId],
    queryFn: () => apiFetch<SiteTariff[]>(`/api/v1/sites/${siteId}/tariffs`),
  });
}

export function useCreateTariff(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateTariffInput) =>
      apiFetch<SiteTariff>(`/api/v1/sites/${siteId}/tariffs`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tariffs", siteId] });
    },
  });
}

export function useEndTariff(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tariffId: number) =>
      apiFetch<SiteTariff>(`/api/v1/sites/${siteId}/tariffs/${tariffId}`, {
        method: "PATCH",
        body: JSON.stringify({ effective_to: new Date().toISOString() }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tariffs", siteId] });
    },
  });
}

export function useDemandWindows(siteId: number, tariffId: number) {
  return useQuery({
    queryKey: ["demand-windows", siteId, tariffId],
    queryFn: () =>
      apiFetch<DemandWindow[]>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/demand-windows`),
  });
}

export function useCreateDemandWindow(siteId: number, tariffId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateDemandWindowInput) =>
      apiFetch<DemandWindow>(`/api/v1/sites/${siteId}/tariffs/${tariffId}/demand-windows`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["demand-windows", siteId, tariffId] });
    },
  });
}

export function useBillingCycles(siteId: number) {
  return useQuery({
    queryKey: ["billing-cycles", siteId],
    queryFn: () => apiFetch<BillingCycle[]>(`/api/v1/sites/${siteId}/billing-cycle`),
  });
}

export function useCreateBillingCycle(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateBillingCycleInput) =>
      apiFetch<BillingCycle>(`/api/v1/sites/${siteId}/billing-cycle`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["billing-cycles", siteId] });
    },
  });
}

export function useEndBillingCycle(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (cycleId: number) =>
      apiFetch<BillingCycle>(`/api/v1/sites/${siteId}/billing-cycle/${cycleId}`, {
        method: "PATCH",
        body: JSON.stringify({ effective_to: new Date().toISOString() }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["billing-cycles", siteId] });
    },
  });
}

export function useDemandCharges(siteId: number) {
  return useQuery({
    queryKey: ["demand-charges", siteId],
    queryFn: () => apiFetch<DemandCharge[]>(`/api/v1/sites/${siteId}/demand-charges`),
  });
}
```

Create `webapp/src/pages/BillingPage.tsx`:

```tsx
import { FormEvent, useState } from "react";
import { useParams } from "react-router-dom";
import {
  useBillingCycles,
  useCreateBillingCycle,
  useCreateDemandWindow,
  useCreateTariff,
  useDemandCharges,
  useDemandWindows,
  useEndBillingCycle,
  useEndTariff,
  useTariffs,
} from "../billing/useBilling";

function parseIntList(raw: string): number[] {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0)
    .map(Number);
}

function DemandWindowsForTariff({ siteId, tariffId }: { siteId: number; tariffId: number }) {
  const { data: windows } = useDemandWindows(siteId, tariffId);
  const [label, setLabel] = useState("");
  const [months, setMonths] = useState("");
  const [daysOfWeek, setDaysOfWeek] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [rate, setRate] = useState("");
  const createWindow = useCreateDemandWindow(siteId, tariffId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createWindow.mutate({
      label,
      months: parseIntList(months),
      days_of_week: parseIntList(daysOfWeek),
      start_time: startTime,
      end_time: endTime,
      rate_c_per_kwh: Number(rate),
    });
  }

  return (
    <div className="ml-4 space-y-2 border-l-2 pl-4">
      <ul className="space-y-1 text-sm">
        {(windows ?? []).map((w) => (
          <li key={w.id}>
            {w.label}: {w.rate_c_per_kwh}c/kWh, {w.start_time}–{w.end_time}
          </li>
        ))}
      </ul>
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Window label" className="border p-1" required />
        <input value={months} onChange={(e) => setMonths(e.target.value)} placeholder="Months (e.g. 12,1,2)" className="border p-1" required />
        <input value={daysOfWeek} onChange={(e) => setDaysOfWeek(e.target.value)} placeholder="Days of week (0=Mon .. 6=Sun)" className="border p-1" required />
        <input value={startTime} onChange={(e) => setStartTime(e.target.value)} placeholder="Start time (HH:MM)" className="border p-1" required />
        <input value={endTime} onChange={(e) => setEndTime(e.target.value)} placeholder="End time (HH:MM)" className="border p-1" required />
        <input value={rate} onChange={(e) => setRate(e.target.value)} placeholder="Rate (cents/kWh)" className="border p-1" required />
        <button type="submit" className="bg-blue-600 p-1 text-white">
          Add demand window
        </button>
      </form>
    </div>
  );
}

function TariffSection({ siteId }: { siteId: number }) {
  const { data: tariffs } = useTariffs(siteId);
  const [serviceAvailability, setServiceAvailability] = useState("");
  const createTariff = useCreateTariff(siteId);
  const endTariff = useEndTariff(siteId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createTariff.mutate({
      effective_from: new Date().toISOString(),
      service_availability_c_per_day: Number(serviceAvailability),
    });
  }

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Tariffs</h2>
      {tariffs && tariffs.length === 0 && <p>No tariffs yet.</p>}
      <ul className="space-y-4">
        {(tariffs ?? []).map((tariff) => (
          <li key={tariff.id} className="space-y-2 border p-2">
            <div className="flex items-center justify-between">
              <span>
                {tariff.service_availability_c_per_day}c/day from{" "}
                {new Date(tariff.effective_from).toLocaleDateString()}
                {tariff.effective_to && ` until ${new Date(tariff.effective_to).toLocaleDateString()}`}
              </span>
              {!tariff.effective_to && (
                <button onClick={() => endTariff.mutate(tariff.id)} className="text-sm underline">
                  End this tariff
                </button>
              )}
            </div>
            <DemandWindowsForTariff siteId={siteId} tariffId={tariff.id} />
          </li>
        ))}
      </ul>
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <input
          value={serviceAvailability}
          onChange={(e) => setServiceAvailability(e.target.value)}
          placeholder="Service availability (cents/day)"
          className="border p-2"
          required
        />
        <button type="submit" className="bg-blue-600 p-2 text-white">
          Add tariff
        </button>
      </form>
    </section>
  );
}

function BillingCycleSection({ siteId }: { siteId: number }) {
  const { data: cycles } = useBillingCycles(siteId);
  const [cycleMonths, setCycleMonths] = useState("");
  const [anchorDay, setAnchorDay] = useState("");
  const createCycle = useCreateBillingCycle(siteId);
  const endCycle = useEndBillingCycle(siteId);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createCycle.mutate({
      effective_from: new Date().toISOString(),
      cycle_months: Number(cycleMonths),
      anchor_day: Number(anchorDay),
    });
  }

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Billing cycle</h2>
      {cycles && cycles.length === 0 && <p>No billing cycles yet.</p>}
      <ul className="space-y-2">
        {(cycles ?? []).map((cycle) => (
          <li key={cycle.id} className="flex items-center justify-between border p-2">
            <span>
              Every {cycle.cycle_months} month(s), anchored on day {cycle.anchor_day}, from{" "}
              {new Date(cycle.effective_from).toLocaleDateString()}
              {cycle.effective_to && ` until ${new Date(cycle.effective_to).toLocaleDateString()}`}
            </span>
            {!cycle.effective_to && (
              <button onClick={() => endCycle.mutate(cycle.id)} className="text-sm underline">
                End this cycle
              </button>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <input
          value={cycleMonths}
          onChange={(e) => setCycleMonths(e.target.value)}
          placeholder="Cycle length (months)"
          className="border p-2"
          required
        />
        <input
          value={anchorDay}
          onChange={(e) => setAnchorDay(e.target.value)}
          placeholder="Anchor day (1-31)"
          className="border p-2"
          required
        />
        <button type="submit" className="bg-blue-600 p-2 text-white">
          Add billing cycle
        </button>
      </form>
    </section>
  );
}

function DemandChargesSection({ siteId }: { siteId: number }) {
  const { data: demandCharges } = useDemandCharges(siteId);

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Demand charges</h2>
      {demandCharges && demandCharges.length === 0 && (
        <p className="text-gray-500">
          No demand charges yet — these are calculated once enough billing-period data has
          been collected.
        </p>
      )}
      <ul className="space-y-2">
        {(demandCharges ?? []).map((charge) => (
          <li key={charge.id} className="border p-2">
            ${(charge.charge_amount / 100).toFixed(2)} for{" "}
            {new Date(charge.billing_period_start).toLocaleDateString()} –{" "}
            {new Date(charge.billing_period_end).toLocaleDateString()}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function BillingPage() {
  const { siteId } = useParams<{ siteId: string }>();
  const parsedSiteId = Number(siteId);

  return (
    <div className="mx-auto mt-8 max-w-2xl space-y-8 px-4">
      <h1 className="text-xl font-semibold">Billing</h1>
      <TariffSection siteId={parsedSiteId} />
      <BillingCycleSection siteId={parsedSiteId} />
      <DemandChargesSection siteId={parsedSiteId} />
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webapp && npm test -- BillingPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/billing/ webapp/src/pages/BillingPage.tsx webapp/src/pages/BillingPage.test.tsx
git commit -m "Add BillingPage: tariffs, demand windows, billing cycles, demand-charges empty state"
```

---

## Task 14: Wire up the real route tree

**Files:**
- Modify: `webapp/src/App.tsx`
- Modify: `webapp/src/App.test.tsx`
- Modify: `webapp/src/main.tsx`

**Interfaces:**
- Consumes: every page component from Tasks 4–13, `AuthProvider`/`ProtectedRoute`
  (Task 3).
- Produces: the final `App` component containing the full `<Routes>` tree. `main.tsx`
  wraps it in `BrowserRouter`, `QueryClientProvider`, and `AuthProvider`.

- [ ] **Step 1: Write the failing tests**

Replace `webapp/src/App.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./api/client";
import { App } from "./App";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App routing", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
  });

  it("redirects an unauthenticated visit to / over to /login", async () => {
    renderAt("/");
    expect(await screen.findByText("Log in")).toBeInTheDocument();
  });

  it("renders the login page at /login", async () => {
    renderAt("/login");
    expect(await screen.findByText("Log in")).toBeInTheDocument();
  });

  it("renders the register page at /register", async () => {
    renderAt("/register");
    expect(await screen.findByText("Create an account")).toBeInTheDocument();
  });

  it("sends an authenticated visit to / through the site picker", async () => {
    localStorage.setItem("access_token", "a-token");
    vi.mocked(apiFetch).mockResolvedValue([]);
    renderAt("/");
    expect(await screen.findByText("Create your first site")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webapp && npm test -- App.test`
Expected: FAIL — `App` still renders the Task 1 placeholder text for every route.

- [ ] **Step 3: Implement**

Replace `webapp/src/App.tsx`:

```tsx
import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { BillingPage } from "./pages/BillingPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { SitePickerPage } from "./pages/SitePickerPage";
import { SourcesPage } from "./pages/SourcesPage";
import { TotpSetupPage } from "./pages/TotpSetupPage";
import { TotpVerifyPage } from "./pages/TotpVerifyPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/totp-setup" element={<TotpSetupPage />} />
      <Route path="/totp-verify" element={<TotpVerifyPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<SitePickerPage />} />
        <Route path="/sites" element={<SitePickerPage />} />
        <Route path="/sites/:siteId" element={<DashboardPage />} />
        <Route path="/sites/:siteId/sources" element={<SourcesPage />} />
        <Route path="/sites/:siteId/billing" element={<BillingPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
```

Replace `webapp/src/main.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import "./index.css";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
);
```

- [ ] **Step 4: Run the full test suite and the build**

Run: `cd webapp && npm test`
Expected: PASS, every test across every task.

Run: `cd webapp && npm run build`
Expected: builds successfully with no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/src/App.tsx webapp/src/App.test.tsx webapp/src/main.tsx
git commit -m "Wire up the full route tree"
```

---

## Task 15: Docker deployment (nginx, runtime config)

**Files:**
- Create: `webapp/Dockerfile`
- Create: `webapp/nginx.conf`
- Create: `webapp/docker-entrypoint.sh`
- Create: `webapp/.env.example`

**Interfaces:**
- Produces: a standalone Docker image serving the built SPA via nginx, independent of
  Core API's container, with the API base URL injected at container start (not at
  build time) — per the spec's "Runtime configuration" section.

This container has no automated test suite — it's a static-file server, verified by
building and running it, matching the mail-relay container's verification approach in
the Core API plan. The "test cycle" is the manual verification in Step 4.

- [ ] **Step 1: Write the nginx config**

Create `webapp/nginx.conf`:

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

(`try_files ... /index.html` is required for a client-side-routed SPA — a direct visit
to e.g. `/sites/7/billing` must serve `index.html`, not a 404, since the route only
exists in React Router's client-side table.)

- [ ] **Step 2: Write the runtime-config entrypoint**

Create `webapp/docker-entrypoint.sh`:

```bash
#!/bin/sh
set -eu

: "${API_BASE_URL:?API_BASE_URL must be set (e.g. https://core-api.yourdomain.example)}"

cat > /usr/share/nginx/html/config.js <<EOF
window.__APP_CONFIG__ = {
  apiBaseUrl: "${API_BASE_URL}"
};
EOF

exec nginx -g 'daemon off;'
```

- [ ] **Step 3: Write the Dockerfile**

Create `webapp/Dockerfile`:

```dockerfile
FROM node:20-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
```

- [ ] **Step 4: Write the env example and manually verify**

Create `webapp/.env.example`:

```
API_BASE_URL=http://localhost:8000
```

Run:
```bash
cd webapp
docker build -t webapp-test .
docker run --rm -p 8080:80 -e API_BASE_URL=http://localhost:8000 webapp-test &
sleep 2
curl -s http://localhost:8080/config.js
curl -s http://localhost:8080/sites/7/billing -o /dev/null -w "%{http_code}\n"
```
Expected: the first `curl` prints `window.__APP_CONFIG__ = { apiBaseUrl: "http://localhost:8000" };`;
the second prints `200` (confirming `try_files` serves `index.html` for a client-routed
path instead of a 404). Stop the background container afterward.

- [ ] **Step 5: Commit**

```bash
cd /Users/stephen/Documents/Coding/.claude/worktrees/frontend-webapp/enphase-data
git add webapp/Dockerfile webapp/nginx.conf webapp/docker-entrypoint.sh webapp/.env.example
git commit -m "Add Docker deployment for the webapp (nginx, runtime API config)"
```

---

## Final check: full suite

- [ ] Run: `cd webapp && npm test`
- [ ] Expected: PASS, every test across every task.
- [ ] Run: `cd webapp && npm run build`
- [ ] Expected: builds cleanly with no TypeScript errors.
