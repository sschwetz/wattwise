# Header / Account Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a top-right header (avatar + name, dropdown with Account settings / Log off) to every protected page of the webapp, backed by a new `display_name` field on `User`.

**Architecture:** Core API gets a nullable `display_name` column plus a dedicated `PATCH /users/me/profile` endpoint, following this router's one-endpoint-per-concern pattern. The webapp gets an `Avatar` component (initials + deterministic color), a `Header` component (avatar/name + dropdown), and a `ProtectedLayout` that wraps every protected route so the header shows everywhere. `SiteLayout`'s sidebar loses its now-redundant "Account" link.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (core-api), React 19 + TanStack Query + react-router-dom + Tailwind v4 + Vitest/RTL (webapp).

**Spec:** `docs/superpowers/specs/2026-10-12-header-account-menu-design.md`

## Global Constraints

- No new error codes — profile update can only fail on auth (existing `401`/`403`) or
  Pydantic validation (empty `display_name`), per the spec's Error handling section.
- No step-up (password/TOTP) required to change `display_name` — it carries no
  account-takeover risk, same treatment as the existing Amber-key `PATCH /users/me`.
- `display_name` is nullable `String(100)`; `UserMeOut.display_name` is `str | None`.
- The header must appear on every protected route (`/`, `/sites`, `/account`,
  `/sites/:siteId/*`), not just inside `SiteLayout`.
- Follow this router's existing one-endpoint-per-concern pattern: `display_name` gets
  its own `PATCH /users/me/profile` route, not folded into the existing Amber-key PATCH.

## Review Focus

- `PATCH /users/me/profile` with `{"display_name": ""}` must be rejected with `422`
  (Pydantic `min_length=1`), not silently accepted as an empty name — pinned in Task 1.
- `GET /users/me` for a user who has never set a name must return `display_name: null`,
  not omit the field or error — pinned in Task 1.
- The header's dropdown must close on outside click and on Escape, and "Log off" must
  actually clear the stored access token (not just close the menu) — pinned in Task 3.
- `Avatar` must not crash or show "undefined" when `display_name` is `null` — it must
  fall back to the email's first letter — pinned in Task 2.
- Nesting `SiteLayout` inside the new `ProtectedLayout` must not produce a double
  `min-h-screen` (header + a full-viewport-tall sidebar/main below it) — pinned in Task 4.

---

### Task 1: Core API — `display_name` field, migration, and profile endpoint

**Files:**
- Modify: `core-api/core_api/models/identity.py` (the `User` class, after the `email` column)
- Modify: `core-api/core_api/schemas.py` (`UserMeOut`, new `UpdateDisplayNameIn`)
- Modify: `core-api/core_api/routers/account.py` (`_to_user_me_out`, new endpoint)
- Create: `core-api/alembic/versions/b4f29d7a1c56_add_display_name_to_users.py`
- Test: `core-api/tests/test_account.py` (append)

**Interfaces:**
- Produces: `User.display_name: str | None`; `UserMeOut.display_name: str | None`;
  `UpdateDisplayNameIn(display_name: str)`; `PATCH /api/v1/users/me/profile` →
  `UserMeOut`.

- [ ] **Step 1: Write the failing tests**

Append to `core-api/tests/test_account.py`:

```python
def test_get_me_reports_null_display_name_when_unset(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.get("/api/v1/users/me", headers=auth(user_id))
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] is None


def test_patch_profile_sets_display_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch(
        "/api/v1/users/me/profile", json={"display_name": "Ada Lovelace"}, headers=auth(user_id)
    )
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Ada Lovelace"

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.display_name == "Ada Lovelace"


def test_patch_profile_rejects_empty_display_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch("/api/v1/users/me/profile", json={"display_name": ""}, headers=auth(user_id))
    assert response.status_code == 422


def test_patch_profile_requires_auth(client, app_and_db_url):
    response = client.patch("/api/v1/users/me/profile", json={"display_name": "Ada Lovelace"})
    assert response.status_code in (401, 422)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd core-api && uv run pytest tests/test_account.py -k display_name -v`
Expected: FAIL — `display_name` missing from `UserMeOut`, and `/profile` route 404s.

- [ ] **Step 3: Add the `display_name` column to `User`**

In `core-api/core_api/models/identity.py`, in the `User` class, right after the
`email` column:

```python
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

- [ ] **Step 4: Create the migration**

Create `core-api/alembic/versions/b4f29d7a1c56_add_display_name_to_users.py`:

```python
"""add display_name to users

Revision ID: b4f29d7a1c56
Revises: c2ba2e5a8811
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4f29d7a1c56'
down_revision: Union[str, Sequence[str], None] = 'c2ba2e5a8811'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('display_name', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'display_name')
```

- [ ] **Step 5: Add the schema changes**

In `core-api/core_api/schemas.py`, change `UserMeOut` (around line 105):

```python
class UserMeOut(BaseModel):
    id: int
    email: str
    display_name: str | None
    # Never the raw key -- just whether one is on file, so the UI can show
    # "configured" without ever having the secret pass back through a response.
    has_amber_api_key: bool
```

Add a new schema right after `UpdateAmberApiKeyIn`:

```python
class UpdateDisplayNameIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
```

- [ ] **Step 6: Add the endpoint**

In `core-api/core_api/routers/account.py`:

Update the import list to include `UpdateDisplayNameIn`:

```python
from core_api.schemas import (
    ChangePasswordIn,
    ConfirmEmailChangeIn,
    ConfirmTotpReenrollIn,
    RequestEmailChangeIn,
    RequestEmailChangeOut,
    StartTotpReenrollIn,
    TotpReenrollConfirmOut,
    TotpReenrollStartOut,
    UpdateAmberApiKeyIn,
    UpdateDisplayNameIn,
    UserMeOut,
)
```

Update `_to_user_me_out`:

```python
def _to_user_me_out(user: User) -> UserMeOut:
    return UserMeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        has_amber_api_key=user.amber_api_key is not None,
    )
```

Add a new endpoint right after `update_amber_api_key`:

```python
@router.patch("/profile", response_model=UserMeOut)
def update_display_name(
    payload: UpdateDisplayNameIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> UserMeOut:
    current_user.display_name = payload.display_name
    db.commit()
    db.refresh(current_user)
    return _to_user_me_out(current_user)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd core-api && uv run pytest tests/test_account.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones — confirms
the `UserMeOut` shape change didn't break the Amber-key tests).

- [ ] **Step 8: Commit**

```bash
git add core-api/core_api/models/identity.py core-api/core_api/schemas.py core-api/core_api/routers/account.py core-api/alembic/versions/b4f29d7a1c56_add_display_name_to_users.py core-api/tests/test_account.py
git commit -m "feat(core-api): add display_name field and profile update endpoint"
```

---

### Task 2: Webapp — `Avatar` component

**Files:**
- Create: `webapp/src/layout/Avatar.tsx`
- Test: `webapp/src/layout/Avatar.test.tsx`

**Interfaces:**
- Produces: `Avatar({ name: string | null, email: string }): JSX.Element` — a circular
  badge showing initials, used by `Header` (Task 3).

- [ ] **Step 1: Write the failing test**

Create `webapp/src/layout/Avatar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Avatar } from "./Avatar";

describe("Avatar", () => {
  it("shows initials from the first two words of a display name", () => {
    render(<Avatar name="Ada Lovelace" email="ada@example.com" />);
    expect(screen.getByText("AL")).toBeInTheDocument();
  });

  it("falls back to the email's first letter when no name is set", () => {
    render(<Avatar name={null} email="ada@example.com" />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("falls back to the email's first letter when the name is blank", () => {
    render(<Avatar name="   " email="ada@example.com" />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("uses a single initial for a one-word name", () => {
    render(<Avatar name="Ada" email="ada@example.com" />);
    expect(screen.getByText("A")).toBeInTheDocument();
  });

  it("picks the same color for the same input across renders", () => {
    const { unmount } = render(<Avatar name="Ada Lovelace" email="ada@example.com" />);
    const firstClassName = screen.getByText("AL").className;
    unmount();

    render(<Avatar name="Ada Lovelace" email="ada@example.com" />);
    expect(screen.getByText("AL").className).toBe(firstClassName);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && npx vitest run src/layout/Avatar.test.tsx`
Expected: FAIL with "Failed to resolve import ./Avatar" (file doesn't exist yet).

- [ ] **Step 3: Implement `Avatar`**

Create `webapp/src/layout/Avatar.tsx`:

```tsx
const PALETTE: { bg: string; text: string }[] = [
  { bg: "bg-solar", text: "text-[#1a1203]" },
  { bg: "bg-flow", text: "text-white" },
  { bg: "bg-grid", text: "text-white" },
  { bg: "bg-danger", text: "text-white" },
];

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function initialsFromName(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]!.toUpperCase())
    .join("");
}

function initialsFromEmail(email: string): string {
  const localPart = email.split("@")[0] ?? "";
  return localPart.length > 0 ? localPart[0]!.toUpperCase() : "?";
}

export interface AvatarProps {
  name: string | null;
  email: string;
}

/** Initials-only avatar -- there is no uploaded-image support yet (see
 * sub-project 4 in docs/superpowers/specs/2026-10-12-header-account-menu-design.md). */
export function Avatar({ name, email }: AvatarProps) {
  const trimmedName = name?.trim();
  const hasName = !!trimmedName;
  const initials = hasName ? initialsFromName(trimmedName) : initialsFromEmail(email);
  const colorKey = hasName ? trimmedName : email;
  const palette = PALETTE[hashString(colorKey) % PALETTE.length]!;

  return (
    <span
      aria-hidden="true"
      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full font-display text-sm font-semibold ${palette.bg} ${palette.text}`}
    >
      {initials}
    </span>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && npx vitest run src/layout/Avatar.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webapp/src/layout/Avatar.tsx webapp/src/layout/Avatar.test.tsx
git commit -m "feat(webapp): add initials-based Avatar component"
```

---

### Task 3: Webapp — `Header` component

**Files:**
- Create: `webapp/src/layout/Header.tsx`
- Test: `webapp/src/layout/Header.test.tsx`

**Interfaces:**
- Consumes: `Avatar({ name, email })` (Task 2); `useMe()` from
  `webapp/src/account/useAccount.ts` (returns `{ data?: Me }` where `Me` now has
  `display_name: string | null`, from Task 1's API change); `useAuth()` from
  `webapp/src/auth/AuthContext.tsx` (returns `{ logout(): void }`, already exists).
- Produces: `Header(): JSX.Element`, used by `ProtectedLayout` (Task 4).

- [ ] **Step 1: Update the `Me` type so `Header` (and `AccountPage`, Task 5) can rely on it**

In `webapp/src/account/useAccount.ts`, update the `Me` interface:

```ts
export interface Me {
  id: number;
  email: string;
  display_name: string | null;
  has_amber_api_key: boolean;
}
```

- [ ] **Step 2: Write the failing test**

Create `webapp/src/layout/Header.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { Header } from "./Header";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

function renderHeader() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/sites/7"]}>
          <Routes>
            <Route path="/sites/7" element={<Header />} />
            <Route path="/account" element={<p>Account page</p>} />
            <Route path="/login" element={<p>Login page</p>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("Header", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
    localStorage.setItem("access_token", "fake-token");
  });

  it("shows the display name when one is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
  });

  it("falls back to the email when no display name is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: null,
      has_amber_api_key: false,
    });
    renderHeader();
    expect(await screen.findByText("ada@example.com")).toBeInTheDocument();
  });

  it("opens a dropdown with Account settings and Log off", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    expect(screen.getByRole("menuitem", { name: "Account settings" })).toHaveAttribute("href", "/account");
    expect(screen.getByRole("menuitem", { name: "Log off" })).toBeInTheDocument();
  });

  it("closes the dropdown on outside click", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    expect(screen.getByRole("menuitem", { name: "Log off" })).toBeInTheDocument();

    await userEvent.click(document.body);
    expect(screen.queryByRole("menuitem", { name: "Log off" })).not.toBeInTheDocument();
  });

  it("closes the dropdown on Escape", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    expect(screen.getByRole("menuitem", { name: "Log off" })).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menuitem", { name: "Log off" })).not.toBeInTheDocument();
  });

  it("clears the stored access token when Log off is clicked", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderHeader();
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByRole("button", { name: /ada lovelace/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Log off" }));

    expect(localStorage.getItem("access_token")).toBeNull();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd webapp && npx vitest run src/layout/Header.test.tsx`
Expected: FAIL with "Failed to resolve import ./Header" (file doesn't exist yet).

- [ ] **Step 4: Implement `Header`**

Create `webapp/src/layout/Header.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { useMe } from "../account/useAccount";
import { useAuth } from "../auth/AuthContext";
import { Avatar } from "./Avatar";

function ChevronIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Header() {
  const { data: me } = useMe();
  const { logout } = useAuth();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const displayName = me?.display_name ?? null;
  const email = me?.email ?? "";
  const label = displayName ?? email;

  return (
    <header className="flex items-center justify-end border-b border-line bg-surface px-4 py-2">
      <div ref={containerRef} className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-haspopup="menu"
          aria-label={label}
          className="flex items-center gap-2 rounded-md px-2 py-1 text-ink hover:bg-surface-2"
        >
          <Avatar name={displayName} email={email} />
          <span className="text-sm font-medium">{label}</span>
          <ChevronIcon />
        </button>
        {open && (
          <div role="menu" className="absolute right-0 top-full z-10 mt-1 w-48 rounded-md border border-line bg-surface py-1 shadow-lg">
            <NavLink
              to="/account"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="block px-3 py-2 text-sm text-ink hover:bg-surface-2"
            >
              Account settings
            </NavLink>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                logout();
              }}
              className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-surface-2"
            >
              Log off
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd webapp && npx vitest run src/layout/Header.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webapp/src/account/useAccount.ts webapp/src/layout/Header.tsx webapp/src/layout/Header.test.tsx
git commit -m "feat(webapp): add Header with account dropdown and log off"
```

---

### Task 4: Webapp — `ProtectedLayout` wiring and `SiteLayout` cleanup

**Files:**
- Create: `webapp/src/layout/ProtectedLayout.tsx`
- Modify: `webapp/src/App.tsx`
- Modify: `webapp/src/layout/SiteLayout.tsx`
- Test: `webapp/src/layout/ProtectedLayout.test.tsx`

**Interfaces:**
- Consumes: `Header` (Task 3).
- Produces: `ProtectedLayout(): JSX.Element`, nested inside `ProtectedRoute` in
  `App.tsx` so every protected page renders under it.

- [ ] **Step 1: Write the failing test**

Create `webapp/src/layout/ProtectedLayout.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import { ProtectedLayout } from "./ProtectedLayout";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

describe("ProtectedLayout", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    localStorage.clear();
    localStorage.setItem("access_token", "fake-token");
  });

  it("renders the header above the matched child route", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "ada@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <MemoryRouter initialEntries={["/dashboard"]}>
            <Routes>
              <Route element={<ProtectedLayout />}>
                <Route path="/dashboard" element={<p>Dashboard content</p>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("Dashboard content")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && npx vitest run src/layout/ProtectedLayout.test.tsx`
Expected: FAIL with "Failed to resolve import ./ProtectedLayout" (file doesn't exist yet).

- [ ] **Step 3: Implement `ProtectedLayout`**

Create `webapp/src/layout/ProtectedLayout.tsx`:

```tsx
import { Outlet } from "react-router-dom";
import { Header } from "./Header";

/** Shared chrome for every protected route: the account header, above
 * whatever page-specific layout (or bare page) the matched route renders. */
export function ProtectedLayout() {
  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <Outlet />
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && npx vitest run src/layout/ProtectedLayout.test.tsx`
Expected: PASS

- [ ] **Step 5: Fix the double `min-h-screen` nesting in `SiteLayout` and remove its now-redundant Account link**

In `webapp/src/layout/SiteLayout.tsx`, `SiteLayout` is about to render inside
`ProtectedLayout`'s `min-h-screen flex-col` wrapper (plus the `Header` above it), so its
own `min-h-screen` would force the page taller than the viewport. Change the outer
`<div>`'s class from:

```
className="flex min-h-screen flex-col bg-bg sm:flex-row"
```

to:

```
className="flex flex-1 flex-col bg-bg sm:flex-row"
```

And remove the now-redundant `Account` link (the header's dropdown covers it) and the
import it no longer needs if `NavLink`'s only remaining uses still require it (they do,
for the per-site links and "Switch site" — no import changes needed). Delete:

```tsx
          <NavLink to="/account" className={secondaryLinkClass}>
            Account
          </NavLink>
```

leaving only the "Switch site" link in that `<div className="sm:mt-auto ...">` block.

- [ ] **Step 6: Pin the height fix and confirm nothing else broke**

Append to `webapp/src/layout/SiteLayout.test.tsx`:

```tsx
  it("fills its parent's height instead of forcing its own full viewport height", () => {
    const { container } = renderLayout();
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("flex-1");
    expect(root.className).not.toContain("min-h-screen");
  });
```

Run: `cd webapp && npx vitest run src/layout/SiteLayout.test.tsx`
Expected: PASS (the new test passes against the Step 5 change; no existing test
asserted the Account link's presence, so none of those needed updating).

- [ ] **Step 7: Wire `ProtectedLayout` into the route tree**

In `webapp/src/App.tsx`, add the import:

```tsx
import { ProtectedLayout } from "./layout/ProtectedLayout";
```

Change the protected route block from:

```tsx
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<SitePickerPage />} />
        <Route path="/sites" element={<SitePickerPage />} />
        <Route path="/account" element={<AccountPage />} />
        <Route path="/sites/:siteId" element={<SiteLayout />}>
          <Route index element={<DashboardPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="billing" element={<BillingPage />} />
        </Route>
      </Route>
```

to:

```tsx
      <Route element={<ProtectedRoute />}>
        <Route element={<ProtectedLayout />}>
          <Route path="/" element={<SitePickerPage />} />
          <Route path="/sites" element={<SitePickerPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/sites/:siteId" element={<SiteLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="sources" element={<SourcesPage />} />
            <Route path="billing" element={<BillingPage />} />
          </Route>
        </Route>
      </Route>
```

- [ ] **Step 8: Run the full webapp test suite**

Run: `cd webapp && npx vitest run`
Expected: PASS — this confirms no other page test (which renders pages standalone,
without `ProtectedLayout`) was affected by the routing change.

- [ ] **Step 9: Commit**

```bash
git add webapp/src/layout/ProtectedLayout.tsx webapp/src/layout/ProtectedLayout.test.tsx webapp/src/layout/SiteLayout.tsx webapp/src/App.tsx
git commit -m "feat(webapp): wire the header into every protected route"
```

---

### Task 5: Webapp — Profile section on `AccountPage`

**Files:**
- Modify: `webapp/src/account/useAccount.ts` (new `useUpdateDisplayName` hook)
- Modify: `webapp/src/pages/AccountPage.tsx` (new `ProfileSection`)
- Modify: `webapp/src/pages/AccountPage.test.tsx`

**Interfaces:**
- Consumes: `PATCH /api/v1/users/me/profile` (Task 1).
- Produces: `useUpdateDisplayName(): UseMutationResult<Me, Error, string>`, used only
  by `ProfileSection`.

- [ ] **Step 1: Update existing `AccountPage` test mocks for the new `Me` shape**

In `webapp/src/pages/AccountPage.test.tsx`, every mocked `apiFetch` resolution of the
form `{ id: 1, email: "me@example.com", has_amber_api_key: ... }` needs
`display_name: null` added, e.g. the first test becomes:

```tsx
  it("shows no key set yet when none is configured", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_amber_api_key: false,
    });
    renderPage();
    expect(await screen.findByText(/no amber api key set yet/i)).toBeInTheDocument();
  });
```

Apply the same `display_name: null` addition to every other `mockResolvedValueOnce`
call in this file whose object shape includes `has_amber_api_key` (there are 9 such
calls across the existing tests).

- [ ] **Step 2: Write the new failing tests**

Append to `webapp/src/pages/AccountPage.test.tsx`:

```tsx
  it("shows the current display name", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: "Ada Lovelace",
      has_amber_api_key: false,
    });
    renderPage();
    expect(await screen.findByText(/current name: ada lovelace/i)).toBeInTheDocument();
  });

  it("shows a placeholder when no display name is set", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      id: 1,
      email: "me@example.com",
      display_name: null,
      has_amber_api_key: false,
    });
    renderPage();
    expect(await screen.findByText(/current name: not set/i)).toBeInTheDocument();
  });

  it("saves a new display name", async () => {
    vi.mocked(apiFetch)
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: null, has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: "Ada Lovelace", has_amber_api_key: false })
      .mockResolvedValueOnce({ id: 1, email: "me@example.com", display_name: "Ada Lovelace", has_amber_api_key: false });
    renderPage();
    await screen.findByText(/current name: not set/i);

    await userEvent.type(screen.getByLabelText("Display name"), "Ada Lovelace");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/users/me/profile",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ display_name: "Ada Lovelace" }) }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `cd webapp && npx vitest run src/pages/AccountPage.test.tsx`
Expected: FAIL — no "Display name" field or "Current name" text exists yet.

- [ ] **Step 4: Add the `useUpdateDisplayName` hook**

In `webapp/src/account/useAccount.ts`, add right after `useUpdateAmberApiKey`:

```ts
export function useUpdateDisplayName() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (displayName: string) =>
      apiFetch<Me>("/api/v1/users/me/profile", {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}
```

- [ ] **Step 5: Add the `ProfileSection` to `AccountPage`**

In `webapp/src/pages/AccountPage.tsx`, update the import line to include
`useUpdateDisplayName`:

```tsx
import { useChangePassword, useConfirmTotpReenroll, useMe, useRequestEmailChange, useStartTotpReenroll, useUpdateAmberApiKey, useUpdateDisplayName } from "../account/useAccount";
```

Add this new component right before `function ChangePasswordSection()`:

```tsx
function ProfileSection() {
  const { data: me } = useMe();
  const [displayName, setDisplayName] = useState("");
  const updateDisplayName = useUpdateDisplayName();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    updateDisplayName.mutate(displayName, {
      onSuccess: () => setDisplayName(""),
    });
  }

  return (
    <section className={cardClass}>
      <h2 className="font-display text-lg font-semibold text-ink">Profile</h2>
      {me && <p className="text-sm text-ink-muted">Current name: {me.display_name ?? "Not set"}</p>}
      {updateDisplayName.isError && (
        <p role="alert" className={errorClass}>
          {updateDisplayName.error instanceof Error
            ? updateDisplayName.error.message
            : "Failed to save your name. Please try again."}
        </p>
      )}
      {updateDisplayName.isSuccess && <p className={successClass}>Saved.</p>}
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <label className="flex flex-col text-sm text-ink-muted">
          Display name
          <input
            aria-label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Your name"
            autoComplete="name"
            spellCheck={false}
            className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
            required
          />
        </label>
        <button type="submit" disabled={updateDisplayName.isPending} className={primaryButtonClass}>
          Save
        </button>
      </form>
    </section>
  );
}
```

Update the `AccountPage` function to render it first:

```tsx
export function AccountPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      <h1 className="font-display text-2xl font-semibold text-ink">Account</h1>
      <ProfileSection />
      <ChangePasswordSection />
      <ChangeEmailSection />
      <ManageTotpSection />
      <AmberApiKeySection />
    </div>
  );
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd webapp && npx vitest run src/pages/AccountPage.test.tsx`
Expected: PASS

- [ ] **Step 7: Run the full webapp test suite**

Run: `cd webapp && npx vitest run`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add webapp/src/account/useAccount.ts webapp/src/pages/AccountPage.tsx webapp/src/pages/AccountPage.test.tsx
git commit -m "feat(webapp): add display-name editing to the Account page"
```

---

## Final verification

- [ ] Run `cd core-api && uv run pytest` — full backend suite passes.
- [ ] Run `cd webapp && npx vitest run` — full frontend suite passes.
- [ ] Run `cd webapp && npm run build` — type-checks and builds cleanly.
- [ ] Manually start the webapp dev server, log in, confirm: the header shows on the
  site picker, the account page, and a per-site dashboard; the dropdown opens/closes;
  "Account settings" navigates to `/account`; "Log off" returns to `/login`; setting a
  display name on the Account page updates the header's name after a refresh.
