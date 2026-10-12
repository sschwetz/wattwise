import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, configureApiClient } from "../api/client";
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

/**
 * A descendant that fires a request from its own mount effect — the thing that
 * used to race AuthProvider's wiring effect, since React flushes passive effects
 * child-before-parent within a commit.
 */
function EagerFetcher() {
  useEffect(() => {
    apiFetch("/api/v1/sites").catch(() => undefined);
  }, []);
  return <p>fetched</p>;
}

function stubFetch(status: number, body: unknown) {
  const mock = vi.fn(
    (_input: string | URL | Request, _init?: RequestInit): Promise<Response> =>
      Promise.resolve({ ok: status < 400, status, json: async () => body } as unknown as Response),
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("AuthProvider api-client wiring", () => {
  beforeEach(() => {
    // Put the module-level client state back to its pristine, never-configured
    // shape so these tests observe this render's wiring and not an earlier one's.
    configureApiClient(
      () => null,
      () => undefined,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("has configured the token getter before a descendant's very first request", async () => {
    localStorage.setItem("access_token", "stored-token");
    const fetchMock = stubFetch(200, {});

    await act(async () => {
      render(
        <AuthProvider>
          <EagerFetcher />
        </AuthProvider>,
      );
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const firstCallInit = fetchMock.mock.calls[0][1];
    const headers = new Headers(firstCallInit?.headers);
    expect(headers.get("Authorization")).toBe("Bearer stored-token");
  });

  // Companion coverage for the other half of the wiring. Note this one does not
  // by itself reproduce the mount-ordering race: apiFetch reads onUnauthorized
  // after awaiting fetch, by which point even a deferred (effect-based) wiring
  // would have landed. The Authorization-header test above is the race's probe.
  it("routes a dead-session 401 through logout, clearing the stored token", async () => {
    localStorage.setItem("access_token", "stored-token");
    // invalid_token is one of the codes the API actually emits for a dead
    // session (deps.py); apiFetch only logs out for those, not for a step-up
    // 401 on an otherwise-valid token.
    stubFetch(401, { code: "invalid_token", message: "Invalid or expired token" });

    await act(async () => {
      render(
        <AuthProvider>
          <EagerFetcher />
        </AuthProvider>,
      );
    });

    expect(localStorage.getItem("access_token")).toBeNull();
  });
});
