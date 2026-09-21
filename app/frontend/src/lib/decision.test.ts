import { describe, it, expect } from "vitest";
import { isIrreversible, type DecisionPoint, type DecisionOption } from "@/lib/decision";

// Smoke test — also proves the Vitest harness, TS, and the `@/` alias all resolve.
describe("isIrreversible", () => {
  const base: DecisionPoint = {
    id: "d1",
    kind: "gate",
    title: "proceed?",
    commit: "reversible",
  } as DecisionPoint;

  it("is false for a reversible commit with no dangerous option", () => {
    expect(isIrreversible(base)).toBe(false);
  });

  it("is true when the decision point commits irreversibly", () => {
    expect(isIrreversible({ ...base, commit: "irreversible" })).toBe(true);
  });

  it("is true when the chosen option's intent is danger", () => {
    const opt = { id: "o1", label: "delete", intent: "danger" } as DecisionOption;
    expect(isIrreversible(base, opt)).toBe(true);
  });
});
