import { describe, expect, it } from "vitest";
import { formatTime, splitCitations } from "./answer-text";

describe("splitCitations", () => {
  it("splits text and markers, including back-to-back markers", () => {
    expect(splitCitations("Open models matter[2][3]. Done[1].")).toEqual([
      { kind: "text", text: "Open models matter" },
      { kind: "cite", n: 2 },
      { kind: "cite", n: 3 },
      { kind: "text", text: ". Done" },
      { kind: "cite", n: 1 },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves text without markers whole, and ignores non-numeric brackets", () => {
    expect(splitCitations("No [source] here.")).toEqual([{ kind: "text", text: "No [source] here." }]);
    expect(splitCitations("")).toEqual([]);
  });
});

describe("formatTime", () => {
  it.each([
    [0, "0:00"],
    [328.9, "5:28"],      // floors: never jump past the quoted moment
    [3725, "1:02:05"],
    [-4, "0:00"],
  ])("%s seconds -> %s", (seconds, expected) => {
    expect(formatTime(seconds)).toBe(expected);
  });
});
