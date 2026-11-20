import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Site } from "../sites/useSites";
import { useSiteAccess } from "./useSiteAccess";

const base: Site = { id: 7, owner_user_id: 1, name: "Home", timezone: "Australia/Sydney", enabled: true, latitude: null, longitude: null, my_role: "owner" };

function access(role: string | undefined) {
  return renderHook(() => useSiteAccess({ ...base, my_role: role } as Site)).result.current;
}

describe("useSiteAccess", () => {
  it.each(["owner", "manager"])("lets a %s edit", (role) => {
    expect(access(role)).toEqual({ readOnly: false, note: null });
  });

  it.each(["viewer", undefined])("makes a %s read-only", (role) => {
    const result = access(role);
    expect(result.readOnly).toBe(true);
    expect(result.note).not.toBeNull();
  });
});
