export type Status =
  | "todo"
  | "doing"
  | "running"
  | "blocked"
  | "done"
  | "succeeded"
  | "awaiting_approval"
  | "awaiting_decision"
  | "stale"
  | "failed"
  | "planned"
  | "ready"
  | "skipped";
export type Task = {
  id: string;
  title: string;
  stage: string;
  phase: string;
  role: string;
  owner?: string;
  status: Status;
  progress?: number | null;
  requirement_ids: string[];
  depends_on: string[];
  artefact_ids: string[];
  start?: string | null;
  end?: string | null;
  due_date?: string;
  [key: string]: any;
};
export type Artefact = {
  id: string;
  path: string;
  title?: string;
  sha256?: string | null;
  [key: string]: any;
};
export type Gate = {
  id: string;
  task_id: string;
  status: string;
  artefact_ids: string[];
  target_hashes: Record<string, string>;
  context_hash?: string;
  [key: string]: any;
};
export type NativeMonitor = {
  status: "ready" | "starting" | "unavailable" | "disabled";
  last_error?: string | null;
  events_read?: number;
};
export type Snapshot = {
  schema_version: number;
  mode: string;
  revision: string | null;
  captured_at: string;
  project: {
    id: string;
    name?: string;
    current_stage: string;
    profile?: string;
    version?: string;
    [key: string]: any;
  };
  phases: { id: string; title: string; stages: string[] }[];
  tasks: Task[];
  artefacts: Artefact[];
  agents: any[];
  relations: any[];
  gates: Gate[];
  logs: any[];
  usage: { tokens: number | null; cost: number | null; [key: string]: any };
  capabilities: {
    approve?: boolean;
    revision?: boolean;
    host_connected?: boolean;
    [key: string]: any;
  };
  host?: {
    connected: boolean;
    native_monitor?: NativeMonitor;
    [key: string]: any;
  };
  [key: string]: any;
};
export type Selection = {
  kind: "task" | "artefact" | "agent" | "requirement";
  id: string;
};
export const PHASES = [
  { id: "01", title: "사업과 요구사항", stages: ["discovery"] },
  { id: "02", title: "제안과 발표", stages: ["proposal"] },
  {
    id: "03",
    title: "설계와 구현",
    stages: ["kickoff", "planning", "design", "publishing", "implementation"],
  },
  { id: "04", title: "검증과 배포", stages: ["verification", "release"] },
  { id: "05", title: "인수와 운영", stages: ["handover", "maintenance"] },
];
export const ROLE: Record<string, string> = {
  orchestrator: "오케스트레이터",
  po: "프로덕트 오너",
  spec: "서비스 기획",
  ia: "정보 설계",
  "proposal-strategist": "제안 전략",
  "proposal-producer": "제안 제작",
  architect: "시스템 아키텍트",
  database: "데이터 아키텍트",
  "design-system": "디자인 시스템",
  ui: "UI 디자이너",
  "ux-writer": "UX 라이터",
  frontend: "프론트엔드",
  backend: "백엔드",
  qa: "QA 엔지니어",
  security: "보안 감사",
  reviewer: "코드 리뷰",
  devops: "DevOps",
  "technical-writer": "테크니컬 라이터",
};
export const labels: Record<string, string> = {
  todo: "대기",
  planned: "대기",
  doing: "진행 중",
  running: "진행 중",
  ready: "실행 준비",
  done: "완료",
  succeeded: "완료",
  blocked: "대기 중",
  failed: "실패",
  awaiting_approval: "판단 대기",
  awaiting_decision: "판단 대기",
  stale: "재검토 필요",
  skipped: "적용 제외",
  approved: "승인됨",
  pending: "검토 대기",
  observed: "활동 추적",
  passed: "AI 검토 통과",
  request_revision: "수정 요청",
  revision_requested: "수정 요청",
};
export const completed = (task: Task) =>
  ["done", "succeeded"].includes(task.status);
export const excluded = (task: Task) => task.status === "skipped";
export const settled = (task: Task) => completed(task) || excluded(task);
export const awaiting = (task: Task) =>
  ["awaiting_approval", "awaiting_decision"].includes(task.status);
export const active = (task: Task) =>
  ["doing", "running"].includes(task.status) &&
  !task.result_pending &&
  !["completed", "idle", "failed"].includes(task.execution_state);
export function phaseOf(task: Task) {
  return (
    task.phase || PHASES.find((p) => p.stages.includes(task.stage))?.id || "03"
  );
}
const object = (value: unknown): value is Record<string, any> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
function fields(
  row: any,
  text: string[],
  lists: string[] = [],
  numbers: string[] = [],
  flags: string[] = [],
) {
  if (!object(row)) throw new Error("스냅샷 항목 형식이 잘못되었습니다.");
  for (const key of text)
    if (row[key] != null && typeof row[key] !== "string")
      throw new Error(`스냅샷 ${key}는 문자열이어야 합니다.`);
  // Categorical strings are used as lookup keys in labels and role dictionaries.
  // Prototype names must not resolve to objects/functions rendered as React children.
  for (const key of ["role", "peer_role", "status", "kind", "type", "provider"])
    if (
      typeof row[key] === "string" &&
      Object.hasOwn(Object.prototype, row[key])
    )
      throw new Error(`스냅샷 ${key} 식별자가 잘못되었습니다.`);
  for (const key of lists)
    if (
      row[key] != null &&
      (!Array.isArray(row[key]) ||
        row[key].some((v: unknown) => typeof v !== "string"))
    )
      throw new Error(`스냅샷 ${key}는 문자열 목록이어야 합니다.`);
  for (const key of numbers)
    if (
      row[key] != null &&
      (typeof row[key] !== "number" ||
        !Number.isFinite(row[key]) ||
        row[key] < 0)
    )
      throw new Error(`스냅샷 ${key}는 유효한 수치여야 합니다.`);
  for (const key of flags)
    if (row[key] != null && typeof row[key] !== "boolean")
      throw new Error(`스냅샷 ${key}는 참/거짓이어야 합니다.`);
}
function list(raw: any, key: string): any[] {
  if (raw[key] == null) return [];
  if (!Array.isArray(raw[key]))
    throw new Error(`스냅샷 ${key} 목록 형식이 잘못되었습니다.`);
  return raw[key];
}
function events(raw: any[]) {
  for (const e of raw)
    fields(
      e,
      [
        "id",
        "event_id",
        "at",
        "timestamp",
        "task_id",
        "agent_id",
        "role",
        "provider",
        "peer_role",
        "kind",
        "summary",
        "path",
        "status",
        "message",
        "action",
        "type",
        "actor",
        "revision",
        "notes",
        "session_id",
        "native_agent_id",
        "runtime_source",
        "source",
      ],
      ["paths"],
      ["seq"],
    );
}
function hashes(value: any) {
  if (
    !object(value) ||
    Object.values(value).some((v) => v !== null && typeof v !== "string")
  )
    throw new Error("스냅샷 파일 해시 형식이 잘못되었습니다.");
}
/** Reject invalid render fields before replacing the last valid workspace. */
export function normalise(raw: any): Snapshot {
  if (
    !object(raw) ||
    raw.schema_version !== 1 ||
    !object(raw.project) ||
    typeof raw.project.id !== "string" ||
    !raw.project.id ||
    !Array.isArray(raw.tasks) ||
    !Array.isArray(raw.artefacts || raw.artifacts)
  )
    throw new Error("지원하는 프로젝트이음 스냅샷이 아닙니다.");
  fields(
    raw,
    ["revision", "captured_at", "capturedAt", "mode", "view_mode"],
    [],
    ["activity_seq"],
  );
  fields(raw.project, [
    "id",
    "name",
    "current_stage",
    "profile",
    "version",
    "project_id",
    "business_type",
    "methodology",
  ]);
  for (const task of [...raw.tasks, ...list(raw, "history_tasks")]) {
    if (
      !object(task) ||
      typeof (task.id || task.task_id) !== "string" ||
      typeof task.status !== "string"
    )
      throw new Error("스냅샷 작업 형식이 잘못되었습니다.");
    fields(
      task,
      [
        "id",
        "task_id",
        "title",
        "next_action",
        "stage",
        "phase",
        "role",
        "owner",
        "status",
        "agent_id",
        "provider",
        "latest_action",
        "waiting_reason",
        "blocked_reason",
        "observed_status",
        "notes",
        "start",
        "end",
        "due_date",
        "actual_start",
        "actual_end",
        "attempt_started_at",
        "attempt_finished_at",
        "last_activity_at",
        "last_seen",
        "run_id",
        "step_id",
        "verification_status",
        "access_status",
        "access_message",
        "ledger_status",
        "execution_state",
        "native_agent_id",
        "session_id",
        "runtime_source",
        "runtime_started_at",
        "runtime_completed_at",
        "runtime_updated_at",
        "display_name",
        "native_started_at",
        "native_finished_at",
      ],
      [
        "requirement_ids",
        "depends_on",
        "artefact_ids",
        "expected_outputs",
        "evidence_refs",
        "inputs",
        "active_native_agent_ids",
      ],
      ["progress", "elapsed_seconds", "generation"],
      ["result_pending"],
    );
    if (
      task.execution_state != null &&
      ![
        "starting",
        "preparing",
        "running",
        "completed",
        "failed",
        "idle",
        "disconnected",
      ].includes(task.execution_state)
    )
      throw new Error("스냅샷 실행 상태가 잘못되었습니다.");
    if (
      task.runtime_source != null &&
      !["host_hook", "codex_rollout"].includes(task.runtime_source)
    )
      throw new Error("스냅샷 실행 출처가 잘못되었습니다.");
    executionRef(task);
    if (task.verification != null) {
      fields(
        task.verification,
        ["status", "mode", "reason", "manifest_sha256", "source"],
        [],
        ["checks", "passed", "failed"],
      );
    }
    if (task.latest_activity != null) events([task.latest_activity]);
    events(list(task, "activity"));
  }
  const artefacts = raw.artefacts || raw.artifacts;
  for (const file of artefacts) {
    if (
      !object(file) ||
      typeof file.id !== "string" ||
      typeof file.path !== "string"
    )
      throw new Error("스냅샷 산출물 형식이 잘못되었습니다.");
    fields(
      file,
      [
        "id",
        "path",
        "title",
        "sha256",
        "type",
        "role",
        "media_type",
        "format",
        "verification_status",
        "access_status",
        "access_message",
      ],
      ["task_ids", "requirement_ids"],
      [],
      [
        "exists",
        "downloadable",
        "download_available",
        "download_allowed",
        "previewable",
      ],
    );
  }
  const agents = list(raw, "agents");
  for (const a of agents) {
    if (!object(a) || typeof a.id !== "string")
      throw new Error("스냅샷 실행자 형식이 잘못되었습니다.");
    fields(
      a,
      [
        "id",
        "name",
        "label",
        "role",
        "provider",
        "status",
        "last_seen",
        "last_activity_at",
        "task_id",
        "notes",
        "native_agent_id",
        "session_id",
        "display_name",
        "runtime_source",
        "source",
        "started_at",
        "completed_at",
        "finished_at",
        "current_action",
        "native_type",
        "parent_id",
        "execution_ref",
      ],
      ["task_ids"],
      [],
      ["superseded", "stale", "observed"],
    );
    if (
      a.native_agent_id != null &&
      (!a.native_agent_id.trim() || !a.session_id?.trim())
    )
      throw new Error("스냅샷 실제 작업자와 세션 연결이 잘못되었습니다.");
    if (
      a.runtime_source != null &&
      !["host_hook", "codex_rollout"].includes(a.runtime_source)
    )
      throw new Error("스냅샷 작업자 실행 출처가 잘못되었습니다.");
  }
  if (raw.current_scope != null) {
    fields(raw.current_scope, ["id", "title", "source", "label"], ["task_ids"]);
    if (
      !raw.current_scope.id ||
      !Array.isArray(raw.current_scope.task_ids) ||
      !["latest_run", "explicit", "project", "task_board"].includes(
        raw.current_scope.source,
      )
    )
      throw new Error("스냅샷 현재 범위가 잘못되었습니다.");
  }
  for (const scope of list(raw, "execution_scopes"))
    fields(
      scope,
      ["id", "title", "source", "label", "created_at", "updated_at"],
      ["task_ids"],
    );
  for (const run of list(raw, "history_runs")) {
    fields(run, [
      "id",
      "run_id",
      "title",
      "status",
      "created_at",
      "updated_at",
      "started_at",
      "completed_at",
    ]);
    for (const step of list(run, "steps"))
      fields(
        step,
        ["id", "step_id", "task_id", "title", "status", "role"],
        [],
        [],
        ["valid", "superseded"],
      );
  }
  for (const session of list(raw, "native_sessions"))
    fields(session, [
      "id",
      "session_id",
      "provider",
      "status",
      "started_at",
      "last_seen",
      "updated_at",
      "finished_at",
      "source",
    ]);
  for (const key of ["summary_counts", "all_summary_counts"])
    if (raw[key] != null)
      fields(
        raw[key],
        [],
        [],
        [
          "total",
          "completed",
          "done",
          "running",
          "active",
          "waiting",
          "blocked",
          "failed",
          "stale",
          "excluded",
          "attention",
          "applicable",
          "percent",
        ],
      );
  const gates = list(raw, "gates");
  for (const g of gates) {
    if (
      !object(g) ||
      typeof g.id !== "string" ||
      typeof g.task_id !== "string" ||
      typeof g.status !== "string"
    )
      throw new Error("스냅샷 검토 대상 형식이 잘못되었습니다.");
    fields(
      g,
      [
        "id",
        "task_id",
        "status",
        "title",
        "context_hash",
        "feedback",
        "review_status",
      ],
      ["artefact_ids", "paths"],
      [],
      ["required"],
    );
    hashes(g.target_hashes);
    for (const d of list(g, "decisions"))
      fields(d, ["id", "gate_id", "actor", "feedback", "decision", "at"]);
  }
  for (const phase of list(raw, "phases")) {
    fields(phase, ["id", "title"], ["stages"]);
    if (typeof phase.id !== "string" || !Array.isArray(phase.stages))
      throw new Error("스냅샷 업무 단계 형식이 잘못되었습니다.");
  }
  for (const r of [...list(raw, "relations"), ...list(raw, "edges")])
    fields(r, [
      "id",
      "source",
      "target",
      "from",
      "to",
      "type",
      "kind",
      "relation",
      "label",
      "evidence",
    ]);
  for (const req of list(raw, "requirements"))
    fields(req, [
      "id",
      "requirement_id",
      "title",
      "description",
      "source",
      "status",
      "priority",
      "acceptance_criteria",
    ]);
  for (const f of list(raw, "feedback"))
    fields(f, [
      "id",
      "task_id",
      "source",
      "target",
      "from_task",
      "to_task",
      "summary",
      "status",
    ]);
  for (const key of ["logs", "events", "activity", "activities"])
    events(list(raw, key));
  for (const c of list(raw, "commands")) {
    fields(
      c,
      [
        "id",
        "task_id",
        "agent_id",
        "provider",
        "role",
        "status",
        "started_at",
        "completed_at",
        "created_at",
        "notes",
        "run_id",
        "step_id",
      ],
      ["evidence_refs"],
      ["generation"],
    );
    executionRef(c);
  }
  for (const r of list(raw, "runners"))
    fields(
      r,
      [
        "id",
        "runner_id",
        "provider",
        "status",
        "last_seen",
        "task_id",
        "notes",
      ],
      [],
      ["pid", "child_pid"],
      ["connected"],
    );
  if (raw.usage != null)
    fields(
      raw.usage,
      ["coverage"],
      [],
      [
        "tokens",
        "cost",
        "cost_usd",
        "observed_attempts",
        "total_attempts",
        "unreported_attempts",
        "known_attempts",
        "unknown_attempts",
        "known_tokens",
        "known_cost_usd",
      ],
    );
  if (raw.capabilities != null)
    fields(
      raw.capabilities,
      [],
      [],
      [],
      ["approve", "revision", "host_connected"],
    );
  if (raw.host != null) {
    fields(
      raw.host,
      ["execution_mode", "latest_activity_at"],
      [],
      [],
      ["connected", "runner_connected", "data_connected"],
    );
    if (raw.host.native_monitor != null) {
      const monitor = raw.host.native_monitor;
      fields(monitor, ["status", "last_error"], [], ["events_read"]);
      if (
        !["ready", "starting", "unavailable", "disabled"].includes(
          monitor.status,
        ) ||
        (monitor.last_error != null && monitor.last_error.length > 280) ||
        (monitor.events_read != null &&
          (!Number.isInteger(monitor.events_read) ||
            monitor.events_read > 1_000_000_000))
      )
        throw new Error("스냅샷 실행 관측 상태 형식이 잘못되었습니다.");
    }
    if (raw.host.supervisor != null)
      fields(
        raw.host.supervisor,
        ["provider", "status", "last_error", "updated_at"],
        [],
        ["attempts", "next_retry_seconds"],
        ["configured"],
      );
  }
  return {
    ...raw,
    schema_version: 1,
    mode: raw.mode || "snapshot",
    revision: raw.revision ?? null,
    captured_at: raw.captured_at || raw.capturedAt || "",
    project: {
      ...raw.project,
      id: raw.project.id,
      name: raw.project.name || raw.project.id,
      current_stage: raw.project.current_stage || "discovery",
    },
    phases: raw.phases?.length ? raw.phases : PHASES,
    tasks: raw.tasks.map(normaliseTask),
    history_tasks: raw.history_tasks?.map(normaliseTask),

    artefacts,
    agents: agents.map((a) => ({
      ...a,
      id: a.id.startsWith("agent:") ? a.id : `agent:${a.id}`,
    })),
    relations: raw.relations || raw.edges || [],
    logs: raw.logs || raw.events || [],
    gates,
    usage: {
      ...(raw.usage || {}),
      tokens: raw.usage?.tokens ?? null,
      cost: raw.usage?.cost ?? raw.usage?.cost_usd ?? null,
    },
    capabilities: raw.capabilities || {},
  };
}

/** Validate execution identities before using them to associate historical receipts. */
function executionRef(row: any) {
  if (
    row.generation != null &&
    (!Number.isInteger(row.generation) || row.generation < 1)
  )
    throw new Error("스냅샷 실행 세대 형식이 잘못되었습니다.");
  if (row.step_ref != null) {
    fields(row.step_ref, ["run_id", "step_id"]);
    if (!row.step_ref.run_id || !row.step_ref.step_id)
      throw new Error("스냅샷 실행 참조 형식이 잘못되었습니다.");
  }
}
function stepRef(row: any) {
  return (
    row.step_ref ||
    (row.run_id || row.step_id
      ? { run_id: row.run_id || null, step_id: row.step_id || null }
      : null)
  );
}
/** Explicit references must match exactly. Old receipts without references retain generation-1 compatibility. */
export function commandMatchesTask(command: any, task: Task) {
  if (
    command.task_id !== task.id ||
    (command.generation ?? 1) !== (task.generation ?? 1)
  )
    return false;
  const ref = stepRef(task);
  if (Object.hasOwn(command, "step_ref")) {
    const candidate = command.step_ref;
    return candidate == null
      ? ref == null
      : Boolean(
          ref &&
          candidate.run_id === ref.run_id &&
          candidate.step_id === ref.step_id,
        );
  }
  if (command.run_id || command.step_id)
    return (
      (command.run_id ?? null) === (ref?.run_id ?? null) &&
      (command.step_id ?? null) === (ref?.step_id ?? null)
    );
  return true;
}
export function commandsForTask(task: Task, snapshot?: Snapshot) {
  return (snapshot?.commands || []).filter((command: any) =>
    commandMatchesTask(command, task),
  );
}

/** Progress counts describe applicable tasks, never estimated effort or hidden work. */
export function hasIssue(task: Task, snapshot?: Snapshot) {
  if (active(task) || completed(task) || excluded(task)) return false;
  const command = commandsForTask(task, snapshot).at(-1);
  return (
    ["failed", "stale"].includes(task.status) ||
    command?.status === "failed" ||
    (!command &&
      (task.generation ?? 1) === 1 &&
      ["failed", "attention"].includes(task.observed_status))
  );
}
export function waiting(task: Task, snapshot?: Snapshot) {
  return (
    !active(task) &&
    !completed(task) &&
    !excluded(task) &&
    !hasIssue(task, snapshot)
  );
}
export function taskSummary(tasks: Task[], snapshot?: Snapshot) {
  const omitted = tasks.filter(excluded).length;
  const applicable = tasks.length - omitted;
  const done = tasks.filter(completed).length;
  return {
    total: tasks.length,
    applicable,
    done,
    excluded: omitted,
    active: tasks.filter(active).length,
    attention: tasks.filter((t) => hasIssue(t, snapshot)).length,
    waiting: tasks.filter((t) => waiting(t, snapshot)).length,
    percent: applicable ? Math.round((done / applicable) * 100) : null,
  };
}
export function verificationOf(task: Task) {
  const value = task.verification?.status || task.verification_status;
  if (excluded(task)) return { state: "excluded", label: "검증 집계 제외" };
  if (task.status === "stale")
    return { state: "unknown", label: "검증 기준 변경" };
  if (value === "passed") {
    if (
      typeof task.verification?.checks !== "number" ||
      task.verification.checks <= 0
    )
      return { state: "unknown", label: "검증 근거 미보고" };
    if (task.verification.source === "supervisor")
      return { state: "passed", label: "등록 검증 통과" };
    if (task.verification.source === "host-reported")
      return { state: "host_reported", label: "호스트 검증 보고" };
    return { state: "unknown", label: "검증 출처 미보고" };
  }
  if (["failed", "integrity_failed"].includes(value))
    return { state: "failed", label: "검증 실패" };
  if (
    ["artefact_only", "artifact_only"].includes(value) ||
    ["artifact_only", "artefact_only"].includes(task.verification?.mode)
  )
    return { state: "artefact_only", label: "파일 검수 · 실행검사 없음" };
  if (["not_run", "unverified"].includes(value))
    return { state: "not_run", label: "실행 검증 안 함" };
  return { state: "unknown", label: "검증 미보고" };
}
export function usageCoverage(snapshot: Snapshot) {
  const usage = snapshot.usage;
  const missing = usage.unreported_attempts ?? usage.unknown_attempts;
  if (
    usage.coverage === "partial" ||
    (typeof missing === "number" && missing > 0)
  )
    return `관측 합계 · 미집계 ${typeof missing === "number" ? missing + "회" : "있음"}`;
  if (
    usage.coverage === "unknown" ||
    (usage.tokens == null && usage.cost == null)
  )
    return "사용량 미집계";
  if (usage.coverage === "known") return "보고된 실행 사용량";
  return "관측 합계 · 수집 범위 미보고";
}

function normaliseTask(t: any): Task {
  return {
    ...t,
    id: t.id || t.task_id,
    title: t.title || t.id || t.task_id,
    role: t.role || "",
    stage: t.stage || "",
    phase: t.phase || "",
    requirement_ids: t.requirement_ids || [],
    depends_on: t.depends_on || [],
    artefact_ids: t.artefact_ids || [],
  };
}
/** Legacy snapshots have no scope and retain their full task board. */
export function currentTasks(snapshot: Snapshot): Task[] {
  if (!snapshot.current_scope) return snapshot.tasks;
  const ids = new Set(snapshot.current_scope.task_ids);
  return snapshot.tasks.filter((task) => ids.has(task.id));
}
export function historyTasks(snapshot: Snapshot): Task[] {
  if (snapshot.history_tasks) return snapshot.history_tasks;
  const ids = new Set(currentTasks(snapshot).map((task) => task.id));
  return snapshot.tasks.filter((task) => !ids.has(task.id));
}
export function executionLabel(task: Task): string | null {
  if (
    completed(task) ||
    excluded(task) ||
    ["stale", "failed"].includes(task.status)
  )
    return null;
  if (
    task.result_pending ||
    (task.execution_state === "completed" &&
      !completed(task) &&
      !excluded(task))
  )
    return "실행 종료 · 결과 확인 중";
  if (task.execution_state === "failed") return "실행 실패";
  if (["starting", "preparing"].includes(task.execution_state))
    return "실행 준비 중";
  if (task.execution_state === "disconnected") return "실행 연결 확인 필요";
  if (task.execution_state === "idle") return "호스트 대기 중";
  return null;
}
/** Count workers, not role assignments. Session scopes prevent reused IDs colliding. */
export function nativeWorkers(snapshot: Snapshot) {
  return [
    ...new Map(
      snapshot.agents
        .filter(
          (agent) =>
            agent.native_agent_id && agent.session_id && !agent.superseded,
        )
        .map((agent) => [
          `${agent.session_id}:${agent.native_agent_id}`,
          agent,
        ]),
    ).values(),
  ];
}
