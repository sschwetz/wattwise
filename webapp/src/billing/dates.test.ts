import { describe, expect, it } from "vitest";
import { addDays, displayEndDate, endOfDayIso, localDate, startOfDayIso, suggestStart } from "./dates";

const SYDNEY = "Australia/Sydney";
const BRISBANE = "Australia/Brisbane";

describe("startOfDayIso", () => {
  it("is 00:00 local on that day", () => {
    expect(startOfDayIso("2026-07-01", SYDNEY)).toBe("2026-06-30T14:00:00.000Z");
    expect(startOfDayIso("2026-07-01", BRISBANE)).toBe("2026-06-30T14:00:00.000Z");
    expect(startOfDayIso("2026-12-01", SYDNEY)).toBe("2026-11-30T13:00:00.000Z");
  });

  it("uses the offset in force that day across the October daylight-saving change", () => {
    expect(startOfDayIso("2026-10-04", SYDNEY)).toBe("2026-10-03T14:00:00.000Z");
    expect(startOfDayIso("2026-10-05", SYDNEY)).toBe("2026-10-04T13:00:00.000Z");
  });
});

describe("endOfDayIso", () => {
  it("is 00:00 local on the following day, so the end date is the last day applied", () => {
    expect(endOfDayIso("2026-06-30", SYDNEY)).toBe(startOfDayIso("2026-07-01", SYDNEY));
  });

  it("is 23 or 25 hours after the start on a daylight-saving day", () => {
    const hours = (Date.parse(endOfDayIso("2026-10-04", SYDNEY)) - Date.parse(startOfDayIso("2026-10-04", SYDNEY))) / 3_600_000;
    expect(hours).toBe(23);
  });
});

describe("localDate and displayEndDate", () => {
  it("localDate gives the calendar day in the zone", () => {
    expect(localDate("2026-06-30T14:00:00.000Z", SYDNEY)).toBe("2026-07-01");
  });

  it("shows the day before a stored end that is at local midnight", () => {
    expect(displayEndDate("2026-06-30T14:00:00.000Z", SYDNEY)).toBe("2026-06-30");
  });

  it("shows the local date of a stored end that is not at local midnight", () => {
    expect(displayEndDate("2026-06-30T04:30:00.000Z", SYDNEY)).toBe("2026-06-30");
  });
});

describe("suggestStart", () => {
  it("is the day after the latest end date", () => {
    const ended = [{ effective_to: endOfDayIso("2026-03-31", SYDNEY) }, { effective_to: endOfDayIso("2026-06-30", SYDNEY) }];
    expect(suggestStart(ended, SYDNEY, "2026-10-21")).toBe("2026-07-01");
  });

  it("is the day after a legacy end that is not at midnight", () => {
    expect(suggestStart([{ effective_to: "2026-06-30T04:30:00.000Z" }], SYDNEY, "2026-10-21")).toBe("2026-07-01");
  });

  it("is today when nothing has ended", () => {
    expect(suggestStart([{ effective_to: null }], SYDNEY, "2026-10-21")).toBe("2026-10-21");
    expect(suggestStart([], SYDNEY, "2026-10-21")).toBe("2026-10-21");
  });
});

describe("addDays", () => {
  it("rolls over month and year ends", () => {
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });
});
