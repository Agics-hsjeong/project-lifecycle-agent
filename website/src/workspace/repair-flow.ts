import type { Snapshot, Task } from "./model";

export type ResultHistoryRow = {
  id: string;
  attempt: number | null;
  status: string;
  summary: string;
  at: string | null;
  sequence: number | null;
  task_id: string | null;
  role: string;
  evidence_hashes: Record<string, string>;
};
type StepHistory = {
  id: string;
  title: string;
  task_id?: string | null;
  role?: string;
  status: string;
  valid?: boolean;
  superseded?: boolean;
  attempts?: number | null;
  result_history?: ResultHistoryRow[];
  history_omitted?: number;
};
type Attempt = {
  step: StepHistory;
  task: Task;
  key: string;
  rows: ResultHistoryRow[];
  start?: ResultHistoryRow;
  failed?: ResultHistoryRow;
  latest: ResultHistoryRow;
};
export type RepairStage = {
  id: string;
  kind: "failure" | "repair" | "recheck";
  task_id: string;
  role: string;
  attempt: number | null;
  status: string;
  current_status: string;
  summary: string;
  latest_summary: string;
  started_at: string | null;
  finished_at: string | null;
  at: string | null;
  sequence: number | null;
  started_sequence: number | null;
  valid: boolean | undefined;
  is_latest: boolean;
};
export type RepairFlow = {
  id: string;
  run_id: string;
  task_id: string;
  title: string;
  qa_attempts_count: number | null;
  failed_attempt_count: number;
  repair_attempt_count: number;
  stages: RepairStage[];
  total_stages: number;
  omitted_stages: number;
  history_omitted: number;
  ordering: "chronological" | "recorded";
  state: "passed" | "running" | "failed" | "stale" | "unresolved";
  ambiguous_repairs: number;
};
const ENGINEERING = new Set(["backend", "frontend", "database", "devops"]);
const terminal = (status: string) => status !== "running";

function attempts(step: StepHistory, task: Task): Attempt[] {
  const groups = new Map<string, Attempt>();
  for (const row of step.result_history || []) {
    // An old outcome without a recorded start never becomes an invented attempt.
    const key =
      row.attempt == null ? `unreported:${row.id}` : `attempt:${row.attempt}`;
    const existing = groups.get(key);
    if (existing) {
      existing.rows.push(row);
      existing.latest = row;
      if (!existing.start && row.status === "running") existing.start = row;
      if (!existing.failed && row.status === "failed") existing.failed = row;
    } else {
      groups.set(key, {
        step,
        task,
        key,
        rows: [row],
        latest: row,
        start: row.status === "running" ? row : undefined,
        failed: row.status === "failed" ? row : undefined,
      });
    }
  }
  return [...groups.values()];
}
function compareRows(
  a?: ResultHistoryRow,
  b?: ResultHistoryRow,
): number | null {
  if (!a || !b) return null;
  if (a.sequence != null && b.sequence != null) return a.sequence - b.sequence;
  const left = Date.parse(a.at || ""),
    right = Date.parse(b.at || "");
  // Equal timestamps without ledger sequence do not establish an order.
  return Number.isFinite(left) && Number.isFinite(right) && left !== right
    ? left - right
    : null;
}
function sharedEvidence(a: ResultHistoryRow, b: ResultHistoryRow): boolean {
  return Object.entries(a.evidence_hashes).some(
    ([file, hash]) =>
      Object.hasOwn(b.evidence_hashes, file) &&
      b.evidence_hashes[file] === hash,
  );
}
function stageOf(
  attempt: Attempt,
  kind: RepairStage["kind"],
  latest: boolean,
): RepairStage {
  const outcome = kind === "failure" ? attempt.failed! : attempt.latest;
  return {
    id: `${attempt.task.id}:${attempt.key}:${kind}`,
    kind,
    task_id: attempt.task.id,
    role: attempt.task.role,
    attempt: outcome.attempt,
    status: outcome.status,
    current_status: attempt.latest.status,
    summary: outcome.summary,
    latest_summary: attempt.latest.summary,
    started_at: attempt.start?.at || null,
    finished_at: terminal(outcome.status) ? outcome.at : null,
    at: outcome.at ?? null,
    sequence: outcome.sequence ?? null,
    started_sequence: attempt.start?.sequence ?? null,
    valid: attempt.step.valid,
    is_latest: latest,
  };
}

/** Read recorded attempts only. Shared project membership is never a repair link. */
export function buildRepairFlows(snapshot: Snapshot): RepairFlow[] {
  const scope = snapshot.current_scope;
  if (!scope?.run_id || !Array.isArray(scope.task_ids)) return [];
  const run = (snapshot.history_runs || []).find(
    (item: any) => item.id === scope.run_id,
  );
  if (!run || !Array.isArray(run.steps)) return [];
  const current = new Set<string>(scope.task_ids);
  const bound = (run.steps as StepHistory[]).flatMap((step) => {
    if (!step.task_id || step.superseded || !Array.isArray(step.result_history))
      return [];
    const task = snapshot.tasks.find(
      (item) => item.id === step.task_id && current.has(item.id),
    );
    const ref =
      task?.run_id != null || task?.step_id != null
        ? { run_id: task.run_id, step_id: task.step_id }
        : task?.step_ref;
    if (
      !task ||
      task.role !== step.role ||
      ref?.run_id !== run.id ||
      ref?.step_id !== step.id
    )
      return [];
    return [{ step, task, attempts: attempts(step, task) }];
  });
  const qa = bound.filter(
    (item) => item.task.role === "qa" && item.attempts.some((a) => a.failed),
  );
  const failures = qa.flatMap((item) =>
    item.attempts.flatMap((attempt, index) =>
      attempt.failed
        ? [{ owner: item, attempt, next: item.attempts[index + 1]?.start }]
        : [],
    ),
  );
  const repairs = bound
    .filter((item) => ENGINEERING.has(item.task.role))
    .flatMap((item) => item.attempts);
  const links = new Map<Attempt, Attempt[]>();
  const ambiguous = new Map<Attempt, Set<Attempt>>();
  for (const repair of repairs) {
    if (!repair.start) continue;
    const matches = failures.filter((failure) => {
      if (!sharedEvidence(failure.attempt.failed!, repair.start!)) return false;
      const afterFailure = compareRows(failure.attempt.failed, repair.start);
      const beforeRetry = compareRows(repair.start, failure.next);
      return (
        (afterFailure == null || afterFailure < 0) &&
        (beforeRetry == null || beforeRetry < 0)
      );
    });
    if (matches.length === 1) {
      const target = matches[0].attempt;
      links.set(target, [...(links.get(target) || []), repair]);
    } else if (matches.length > 1) {
      ambiguous.set(repair, new Set(matches.map((match) => match.attempt)));
    }
  }
  return qa.map((item) => {
    const first = item.attempts.findIndex((attempt) => attempt.failed);
    const shown = item.attempts.slice(first);
    const allStages: RepairStage[] = [];
    const involvedSteps = new Set<StepHistory>([item.step]);
    let repairCount = 0;
    for (const attempt of shown) {
      allStages.push(
        stageOf(
          attempt,
          attempt.failed ? "failure" : "recheck",
          attempt === item.attempts.at(-1),
        ),
      );
      for (const repair of links.get(attempt) || []) {
        repairCount++;
        involvedSteps.add(repair.step);
        allStages.push(
          stageOf(
            repair,
            "repair",
            repair.latest === repair.step.result_history?.at(-1),
          ),
        );
      }
    }
    const allSequences = allStages.every((stage) => stage.sequence != null);
    const timestamps = allStages.map((stage) => Date.parse(stage.at || ""));
    const distinctTimes =
      timestamps.every(Number.isFinite) &&
      new Set(timestamps).size === timestamps.length;
    const chronological = allSequences || distinctTimes;
    if (chronological)
      allStages.sort((a, b) =>
        allSequences
          ? a.sequence! - b.sequence!
          : Date.parse(a.at!) - Date.parse(b.at!),
      );
    const latest = item.attempts.at(-1)!.latest;
    let state: RepairFlow["state"] = "unresolved";
    if (latest.status === item.step.status) {
      if (latest.status === "running") state = "running";
      else if (latest.status === "failed") state = "failed";
      else if (latest.status === "succeeded")
        state =
          item.step.valid === true
            ? "passed"
            : item.step.valid === false
              ? "stale"
              : "unresolved";
    }
    if (
      allStages.some(
        (stage) =>
          stage.kind === "repair" &&
          stage.is_latest &&
          stage.current_status === "running",
      )
    )
      state = "running";
    return {
      id: `${run.id}:${item.task.id}`,
      run_id: run.id,
      task_id: item.task.id,
      title: item.task.title,
      qa_attempts_count: item.step.attempts ?? null,
      failed_attempt_count: item.attempts.filter((attempt) =>
        Boolean(attempt.failed),
      ).length,
      repair_attempt_count: repairCount,
      stages: allStages.slice(-12),
      total_stages: allStages.length,
      omitted_stages: Math.max(0, allStages.length - 12),
      history_omitted: [...involvedSteps].reduce(
        (total, step) => total + (step.history_omitted || 0),
        0,
      ),
      ordering: chronological ? "chronological" : "recorded",
      state,
      ambiguous_repairs: [...ambiguous.values()].filter((matches) =>
        shown.some((attempt) => matches.has(attempt)),
      ).length,
    };
  });
}
