import React from "react";
import type { Task } from "./model";
import { verificationOf } from "./model";
export function VerificationBadge({ task }: { task: Task }) {
  const state = verificationOf(task);
  return (
    <span
      className={`verification-state verification-${state.state}`}
      data-verification={state.state}
    >
      <span aria-hidden="true">
        {state.state === "passed" ? "✓" : state.state === "failed" ? "!" : "·"}
      </span>
      {state.label}
    </span>
  );
}
