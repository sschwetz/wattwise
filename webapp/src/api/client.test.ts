import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, configureApiClient, errorMessage } from "./client";

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

/** A response with no JSON body at all -- e.g. a 401 synthesized by a proxy
 * rather than by the API, where there is no code to inspect. */
function mockUnparseableFetchOnce(status: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      statusText: "Unauthorized",
      json: async () => {
        throw new SyntaxError("Unexpected end of JSON input");
      },
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

  it("does not overwrite an explicitly-passed Authorization header with the configured token", async () => {
    configureApiClient(() => "the-configured-token", () => {});
    mockFetchOnce(200, {});
    await apiFetch("/api/v1/auth/totp/setup/start", {
      headers: { Authorization: "Bearer explicit-scoped-token" },
    });
    const [, requestInit] = vi.mocked(fetch).mock.calls[0];
    const headers = requestInit!.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer explicit-scoped-token");
  });

  it("calls onUnauthorized on a 401 without attaching a previous token forever", async () => {
    const onUnauthorized = vi.fn();
    configureApiClient(() => "stale-token", onUnauthorized);
    mockFetchOnce(401, { code: "invalid_token", message: "Invalid or expired token", detail: null });
    await expect(apiFetch("/api/v1/sites")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("calls onUnauthorized on a 401 with a missing_token code", async () => {
    const onUnauthorized = vi.fn();
    configureApiClient(() => null, onUnauthorized);
    mockFetchOnce(401, { code: "missing_token", message: "Authorization header is required", detail: null });
    await expect(apiFetch("/api/v1/sites")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("calls onUnauthorized on a bodyless 401, where the code is indeterminable", async () => {
    const onUnauthorized = vi.fn();
    configureApiClient(() => "stale-token", onUnauthorized);
    mockUnparseableFetchOnce(401);
    await expect(apiFetch("/api/v1/sites")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  // A step-up 401 (wrong current password / wrong-or-rolled-over TOTP code on a
  // password, email-change or TOTP-reenroll mutation) says nothing about the
  // access_token: logging the user out there destroys the session and redirects
  // to /login before the component can render its error banner.
  it("does not log the user out on a step-up 401 (invalid_code)", async () => {
    let loggedOut = false;
    configureApiClient(
      () => "a-valid-token",
      () => {
        loggedOut = true;
      },
    );
    mockFetchOnce(401, { code: "invalid_code", message: "Incorrect verification code", detail: null });

    await expect(apiFetch("/api/v1/users/me/password", { method: "POST" })).rejects.toThrow(
      "Incorrect verification code",
    );
    expect(loggedOut).toBe(false);
  });

  it("does not log the user out on a step-up 401 (invalid_credentials)", async () => {
    let loggedOut = false;
    configureApiClient(
      () => "a-valid-token",
      () => {
        loggedOut = true;
      },
    );
    mockFetchOnce(401, { code: "invalid_credentials", message: "Current password is incorrect", detail: null });

    await expect(
      apiFetch("/api/v1/users/me/email/change/request", { method: "POST" }),
    ).rejects.toThrow("Current password is incorrect");
    expect(loggedOut).toBe(false);
  });
});

describe("errorMessage", () => {
  it("returns an Error's message", () => {
    expect(errorMessage(new ApiError(401, "invalid_code", "Incorrect verification code", null), "fallback")).toBe(
      "Incorrect verification code",
    );
  });

  it("returns the fallback for a non-Error throwable", () => {
    expect(errorMessage("just a string", "fallback")).toBe("fallback");
  });
});
