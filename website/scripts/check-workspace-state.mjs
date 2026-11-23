import "./check-repair-flow.mjs";
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  normalise,
  completed,
  excluded,
  settled,
} from "../src/workspace/model.ts";
import {
  LatestRequest,
  isSnapshotBusy,
} from "../src/workspace/request-state.ts";
import {
  taskSummary,
  verificationOf,
  usageCoverage,
  currentTasks,
  historyTasks,
  executionLabel,
  nativeWorkers,
  commandMatchesTask,
  commandsForTask,
  hasIssue,
} from "../src/workspace/model.ts";
const fixture = () => ({
  schema_version: 1,
  revision: "r1",
  captured_at: "2026-11-21T00:00:00Z",
  project: { id: "test", name: "실행 기록" },
  tasks: [
    {
      id: "TASK-1",
      status: "running",
      role: "backend",
      title: "구현",
      depends_on: [],
      requirement_ids: [],
      artefact_ids: ["F-1"],
    },
  ],
  artefacts: [{ id: "F-1", path: "src/app.py", sha256: "hash1" }],
  agents: [],
  gates: [],
  usage: { tokens: null, cost: null },
});
test("valid snapshot keeps actual timestamps, unknown usage and original input", () => {
  const raw = fixture();
  const s = normalise(raw);
  assert.equal(s.captured_at, raw.captured_at);
  assert.equal(s.usage.cost, null);
  assert.equal(s.tasks[0].role, "backend");
  assert.notEqual(s, raw);
  assert.notEqual(s.tasks[0], raw.tasks[0]);
  assert.equal(raw.tasks[0].phase, undefined);
});
test("missing capture time stays unreported and ID fallback remains readable", () => {
  const raw = fixture();
  delete raw.captured_at;
  delete raw.project.name;
  const s = normalise(raw);
  assert.equal(s.captured_at, "");
  assert.equal(s.project.name, "test");
});
const invalid = [
  ["project name", (s) => (s.project.name = { invalid: true })],
  ["project stage", (s) => (s.project.current_stage = [])],
  ["revision", (s) => (s.revision = {})],
  ["captured time", (s) => (s.captured_at = 10)],
  ["task title", (s) => (s.tasks[0].title = {})],
  ["task role", (s) => (s.tasks[0].role = [])],
  ["prototype role", (s) => (s.tasks[0].role = "__proto__")],
  ["prototype status", (s) => (s.tasks[0].status = "constructor")],
  [
    "prototype activity kind",
    (s) => (s.activity = [{ summary: "작업", kind: "__proto__" }]),
  ],
  ["task action", (s) => (s.tasks[0].latest_action = {})],
  ["task dependency", (s) => (s.tasks[0].depends_on = [{}])],
  ["task elapsed", (s) => (s.tasks[0].elapsed_seconds = "17")],
  ["task activity", (s) => (s.tasks[0].activity = [{ summary: {} }])],
  ["artefact hash", (s) => (s.artefacts[0].sha256 = {})],
  ["artefact label", (s) => (s.artefacts[0].title = {})],
  ["phase list", (s) => (s.phases = {})],
  ["phase stages", (s) => (s.phases = [{ id: "03", stages: {} }])],
  ["agent label", (s) => (s.agents = [{ id: "a", name: {} }])],
  ["agent task IDs", (s) => (s.agents = [{ id: "a", task_ids: [{}] }])],
  ["activity role", (s) => (s.activity = [{ summary: "작업", role: {} }])],
  ["activity paths", (s) => (s.activity = [{ summary: "작업", paths: [{}] }])],
  ["logs action", (s) => (s.logs = [{ action: {} }])],
  ["logs type", (s) => (s.logs = [{ type: {} }])],
  ["runner status", (s) => (s.runners = [{ id: "r", status: {} }])],
  ["command role", (s) => (s.commands = [{ id: "c", role: {} }])],
  [
    "requirement label",
    (s) => (s.requirements = [{ id: "q", description: {} }]),
  ],
  [
    "relation endpoint",
    (s) => (s.relations = [{ source: {}, target: "TASK-1" }]),
  ],
  [
    "gate target hash",
    (s) =>
      (s.gates = [
        {
          id: "g",
          task_id: "TASK-1",
          status: "pending",
          target_hashes: { "src/app.py": {} },
        },
      ]),
  ],
  ["usage", (s) => (s.usage.tokens = "100")],
  ["host liveness", (s) => (s.host = { connected: {} })],
  ["supervisor error", (s) => (s.host = { supervisor: { last_error: {} } })],
];
for (const [name, change] of invalid)
  test(`reject invalid rendered field: ${name}`, () => {
    const raw = fixture();
    change(raw);
    assert.throws(() => normalise(raw), /스냅샷/);
  });
test("server supervisor health and native unknown timestamps are accepted", () => {
  const raw = fixture();
  raw.host = {
    connected: false,
    runner_connected: false,
    supervisor: {
      configured: true,
      provider: "codex",
      status: "retrying",
      attempts: 2,
      last_error: "소유권 확인",
      next_retry_seconds: 4,
      updated_at: "2026-11-21T00:00:00Z",
    },
  };
  raw.agents = [
    { id: "native", provider: "codex", task_id: "TASK-1", last_seen: null },
  ];
  const s = normalise(raw);
  assert.equal(s.agents[0].id, "agent:native");
  assert.equal(s.host.supervisor.status, "retrying");
});
test("overlapping automatic polls do not starve a slow response", () => {
  const gate = new LatestRequest();
  const first = gate.begin();
  assert(first);
  assert.equal(gate.begin(), null);
  assert.equal(gate.isCurrent(first), true);
  assert.equal(first.controller.signal.aborted, false);
  gate.finish(first);
  assert(gate.begin());
});
test("forced refresh cancels older snapshot and older completion cannot clear successor", () => {
  const gate = new LatestRequest();
  const old = gate.begin();
  const next = gate.begin(true);
  assert.equal(old.controller.signal.aborted, true);
  assert.equal(gate.isCurrent(old), false);
  assert.equal(gate.owns(old), false);
  gate.finish(old);
  assert.equal(gate.isCurrent(next), true);
  assert.equal(gate.begin(), null);
});
test("timeout retains ownership so current error is visible, stale errors are ignored", () => {
  const gate = new LatestRequest();
  const ticket = gate.begin();
  ticket.controller.abort();
  assert.equal(gate.isCurrent(ticket), false);
  assert.equal(gate.owns(ticket), true);
  const successor = gate.begin(true);
  assert.equal(gate.owns(ticket), false);
  assert.equal(gate.isCurrent(successor), true);
});
test("import, changed path or unmount cancels all outstanding callbacks", () => {
  const gate = new LatestRequest();
  const ticket = gate.begin();
  gate.cancel();
  assert.equal(ticket.controller.signal.aborted, true);
  assert.equal(gate.isCurrent(ticket), false);
  assert.equal(gate.owns(ticket), false);
  const next = gate.begin();
  assert(next.id > ticket.id);
});

test("known ledger conflict preserves a previously received connection state", () => {
  assert.equal(isSnapshotBusy({ status: 409, code: "conflict" }, true), true);
  assert.equal(isSnapshotBusy({ status: 409, code: "conflict" }, false), false);
});
test("network, invalid data and unknown 409 responses are not treated as ledger busy", () => {
  for (const error of [
    null,
    new Error("offline"),
    { status: 503, code: "conflict" },
    { status: 409 },
    { status: 400, code: "invalid_request" },
  ])
    assert.equal(isSnapshotBusy(error, true), false);
});

test("scope progress separates completed, excluded, failed and ordinary waiting", () => {
  const make = (id, status) => ({
    ...normalise(fixture()).tasks[0],
    id,
    status,
  });
  const tasks = [
    make("done", "done"),
    make("excluded", "skipped"),
    make("error", "failed"),
  ];
  assert.deepEqual(taskSummary(tasks), {
    total: 3,
    applicable: 2,
    done: 1,
    recorded_done: 1,
    recorded_applicable: 2,
    recorded_percent: 50,
    verification_unavailable: 0,
    excluded: 1,
    active: 0,
    attention: 1,
    waiting: 0,
    percent: 50,
  });
  assert.equal(completed(tasks[1]), false);
  assert.equal(excluded(tasks[1]), true);
  assert.equal(settled(tasks[1]), true);
  assert.equal(taskSummary([tasks[1]]).percent, null);
  assert.equal(taskSummary([]).percent, null);
});
test("task completion never invents a passed verification result", () => {
  const task = { ...normalise(fixture()).tasks[0], status: "done" };
  assert.equal(verificationOf(task).state, "unknown");
  assert.equal(
    verificationOf({ ...task, verification: { status: "passed", checks: 0 } })
      .state,
    "unknown",
  );
  assert.equal(
    verificationOf({
      ...task,
      verification: { status: "passed", checks: 1, source: "supervisor" },
    }).state,
    "passed",
  );
  assert.equal(
    verificationOf({
      ...task,
      verification: { status: "artifact_only", mode: "artifact_only" },
    }).state,
    "artefact_only",
  );
  assert.equal(
    verificationOf({
      ...task,
      status: "stale",
      verification: { status: "passed" },
    }).state,
    "unknown",
  );
});
test("host-reported verification never claims independent supervisor checks", () => {
  const task = { ...normalise(fixture()).tasks[0], status: "done" };
  assert.deepEqual(
    verificationOf({
      ...task,
      verification: { status: "passed", checks: 2, source: "host-reported" },
    }),
    { state: "host_reported", label: "호스트 검증 보고" },
  );
  assert.deepEqual(
    verificationOf({ ...task, verification: { status: "passed", checks: 2 } }),
    { state: "unknown", label: "검증 출처 미보고" },
  );
  assert.equal(
    verificationOf({
      ...task,
      verification: { status: "passed", checks: 2, source: "untrusted" },
    }).state,
    "unknown",
  );
});
test("partial usage is an observed subtotal with unreported attempts", () => {
  const raw = fixture();
  raw.usage = {
    tokens: null,
    cost: null,
    known_tokens: 100,
    known_cost_usd: 0.1,
    coverage: "partial",
    observed_attempts: 1,
    total_attempts: 2,
    unreported_attempts: 1,
  };
  const s = normalise(raw);
  assert.equal(s.usage.tokens, null);
  assert.equal(s.usage.known_tokens, 100);
  assert.match(usageCoverage(s), /미집계 1회/);
});
test("invalid verification and usage coverage render fields are rejected", () => {
  const raw = fixture();
  raw.tasks[0].verification = { status: { invalid: true } };
  assert.throws(() => normalise(raw), /스냅샷/);
  const second = fixture();
  second.usage.unreported_attempts = -1;
  assert.throws(() => normalise(second), /스냅샷/);
});

test("current scope excludes old failed and stale receipts without erasing history", () => {
  const raw = fixture();
  raw.tasks[0].status = "done";
  raw.tasks.push(
    { ...raw.tasks[0], id: "OLD-FAILED", status: "failed" },
    { ...raw.tasks[0], id: "OLD-STALE", status: "stale" },
  );
  raw.current_scope = {
    id: "RUN-3",
    title: "현재 구현",
    source: "latest_run",
    task_ids: ["TASK-1"],
  };
  raw.history_tasks = raw.tasks.slice(1);
  const s = normalise(raw);
  assert.equal(taskSummary(currentTasks(s)).percent, 100);
  assert.equal(taskSummary(s.tasks).percent, 33);
  assert.deepEqual(
    historyTasks(s).map((t) => t.status),
    ["failed", "stale"],
  );
  raw.current_scope.task_ids = ["OLD-FAILED"];
  assert.equal(taskSummary(currentTasks(normalise(raw))).attention, 1);
});
test("ended native execution does not imply verified task completion", () => {
  const raw = fixture();
  Object.assign(raw.tasks[0], {
    status: "todo",
    ledger_status: "todo",
    execution_state: "completed",
    result_pending: true,
    native_agent_id: "/root/reviewer",
    session_id: "session-1",
    runtime_source: "codex_rollout",
  });
  const task = normalise(raw).tasks[0];
  assert.equal(executionLabel(task), "실행 종료 · 결과 확인 중");
  assert.equal(completed(task), false);
  assert.equal(taskSummary([task]).percent, 0);
});
test("native workers deduplicate role assignments only inside the same session", () => {
  const raw = fixture();
  raw.agents = [
    {
      id: "a",
      native_agent_id: "/root/reviewer",
      session_id: "s1",
      role: "qa",
    },
    {
      id: "b",
      native_agent_id: "/root/reviewer",
      session_id: "s1",
      role: "reviewer",
    },
    { id: "c", native_agent_id: "/root/reviewer", session_id: "s2" },
    { id: "alias", name: "claude-main" },
  ];
  assert.equal(nativeWorkers(normalise(raw)).length, 2);
});
for (const [label, mutate] of [
  ["native ID", (s) => (s.agents = [{ id: "a", native_agent_id: {} }])],
  ["session ID", (s) => (s.tasks[0].session_id = [])],
  [
    "native execution state",
    (s) => (s.tasks[0].execution_state = "pretend-running"),
  ],
  ["runtime source", (s) => (s.tasks[0].runtime_source = "invented")],
  ["pending result", (s) => (s.tasks[0].result_pending = "yes")],
  [
    "scope task IDs",
    (s) =>
      (s.current_scope = {
        id: "s",
        title: "범위",
        source: "latest_run",
        task_ids: [{}],
      }),
  ],
  ["history status", (s) => (s.history_tasks = [{ id: "old", status: {} }])],
  ["history runs", (s) => (s.history_runs = [{ id: "r", status: {} }])],
  ["session state", (s) => (s.native_sessions = [{ session_id: [] }])],
])
  test(`reject malformed native/scope field: ${label}`, () => {
    const raw = fixture();
    mutate(raw);
    assert.throws(() => normalise(raw), /스냅샷/);
  });

test("superseded execution workers cannot inflate current native worker count", () => {
  const raw = fixture();
  raw.agents = [
    {
      id: "old",
      native_agent_id: "old-actor",
      session_id: "s1",
      superseded: true,
    },
    { id: "current", native_agent_id: "current-actor", session_id: "s1" },
  ];
  assert.equal(nativeWorkers(normalise(raw)).length, 1);
});

test("terminal task results keep their evidence status after native lifecycle ends", () => {
  for (const status of ["done", "succeeded", "failed", "stale", "skipped"])
    for (const execution_state of ["idle", "completed"])
      assert.equal(
        executionLabel({ ...fixture().tasks[0], status, execution_state }),
        null,
      );
});

test("CLI receipts match current generation and explicit RUN/STEP references", () => {
  const task = {
    ...fixture().tasks[0],
    status: "todo",
    generation: 2,
    step_ref: { run_id: "RUN-2", step_id: "STEP-2" },
    run_id: "RUN-2",
    step_id: "STEP-2",
  };
  const current = {
    task_id: task.id,
    generation: 2,
    step_ref: { run_id: "RUN-2", step_id: "STEP-2" },
    status: "failed",
  };
  assert.equal(commandMatchesTask(current, task), true);
  assert.equal(commandMatchesTask({ ...current, generation: 1 }, task), false);
  assert.equal(
    commandMatchesTask(
      { ...current, step_ref: { run_id: "RUN-1", step_id: "STEP-2" } },
      task,
    ),
    false,
  );
  assert.equal(commandMatchesTask({ ...current, step_ref: null }, task), false);
  const flat = {
    task_id: task.id,
    generation: 2,
    run_id: "RUN-2",
    step_id: "STEP-2",
  };
  assert.equal(commandMatchesTask(flat, task), true);
  assert.equal(
    commandMatchesTask({ ...flat, step_id: "STEP-OLD" }, task),
    false,
  );
  assert.equal(
    commandMatchesTask({ ...current, task_id: "OTHER" }, task),
    false,
  );
});
test("old CLI failures cannot contaminate rebound tasks, while current failure remains actionable", () => {
  const raw = fixture();
  Object.assign(raw.tasks[0], {
    status: "todo",
    generation: 2,
    step_ref: { run_id: "RUN-2", step_id: "STEP-2" },
    observed_status: "failed",
  });
  raw.commands = [
    { id: "old", task_id: "TASK-1", generation: 1, status: "failed" },
  ];
  const s = normalise(raw);
  assert.equal(commandsForTask(s.tasks[0], s).length, 0);
  assert.equal(hasIssue(s.tasks[0], s), false);
  assert.equal(taskSummary(s.tasks, s).waiting, 1);
  raw.commands.push({
    id: "new",
    task_id: "TASK-1",
    generation: 2,
    status: "failed",
    step_ref: { run_id: "RUN-2", step_id: "STEP-2" },
  });
  const next = normalise(raw);
  assert.equal(hasIssue(next.tasks[0], next), true);
  assert.equal(next.commands.length, 2);
});
test("legacy CLI receipts without execution references keep first-generation compatibility", () => {
  const task = normalise(fixture()).tasks[0];
  task.status = "todo";
  assert.equal(
    commandMatchesTask({ task_id: task.id, status: "failed" }, task),
    true,
  );
  const scoped = {
    ...task,
    generation: 1,
    step_ref: { run_id: "RUN-1", step_id: "STEP-1" },
  };
  assert.equal(
    commandMatchesTask({ task_id: task.id, generation: 1 }, scoped),
    true,
  );
  assert.equal(
    commandMatchesTask({ task_id: task.id, generation: 2 }, task),
    false,
  );
});
for (const [label, change] of [
  [
    "task step ref",
    (s) => (s.tasks[0].step_ref = { run_id: [], step_id: "s" }),
  ],
  [
    "command step ref",
    (s) =>
      (s.commands = [
        { id: "c", task_id: "TASK-1", step_ref: { run_id: "r" } },
      ]),
  ],
  ["generation", (s) => (s.tasks[0].generation = 0)],
  [
    "command generation",
    (s) => (s.commands = [{ id: "c", task_id: "TASK-1", generation: "2" }]),
  ],
])
  test(`reject malformed execution identity: ${label}`, () => {
    const s = fixture();
    change(s);
    assert.throws(() => normalise(s), /스냅샷/);
  });

test("native observation health is typed independently of the data connection", () => {
  for (const status of [
    "ready",
    "starting",
    "waiting",
    "unavailable",
    "disabled",
    "stopped",
  ]) {
    const raw = fixture();
    raw.host = {
      connected: true,
      data_connected: true,
      native_monitor: {
        status,
        last_error:
          status === "unavailable"
            ? "네이티브 실행 기록 연결을 확인하세요. 보관된 이벤트는 유지합니다."
            : null,
        events_read: 0,
      },
    };
    const s = normalise(raw);
    assert.equal(s.host.connected, true);
    assert.equal(s.host.native_monitor.status, status);
    assert.equal(s.host.native_monitor.events_read, 0);
  }
  const raw = fixture();
  raw.host = {
    connected: true,
    native_monitor: { status: "starting", last_error: null },
  };
  assert.equal(normalise(raw).host.native_monitor.status, "starting");
});
for (const [name, monitor] of [
  ["monitor status", { status: "pretend-connected" }],
  [
    "monitor invalid wait time",
    { status: "waiting", waiting_since: "yesterday" },
  ],
  [
    "monitor no time zone",
    { status: "waiting", last_success_at: "2026-11-23T04:00:00" },
  ],
  [
    "monitor unknown error code",
    { status: "unavailable", error_code: "raw private path" },
  ],
  ["monitor retry negative", { status: "waiting", retry_seconds: -1 }],
  ["monitor retry bound", { status: "waiting", retry_seconds: 61 }],

  ["monitor error type", { status: "unavailable", last_error: {} }],
  [
    "monitor error bound",
    { status: "unavailable", last_error: "x".repeat(281) },
  ],
  ["monitor count negative", { status: "ready", events_read: -1 }],
  ["monitor count fraction", { status: "ready", events_read: 0.5 }],
  ["monitor count bound", { status: "ready", events_read: 1_000_000_001 }],
  ["monitor count type", { status: "ready", events_read: "1" }],
])
  test(`reject malformed native monitor: ${name}`, () => {
    const raw = fixture();
    raw.host = { connected: true, native_monitor: monitor };
    assert.throws(() => normalise(raw), /스냅샷/);
  });

test("native monitor wait metadata preserves the last successful receive time", () => {
  const raw = fixture();
  raw.host = {
    connected: true,
    native_monitor: {
      status: "waiting",
      last_error: null,
      error_code: null,
      waiting_since: "2026-11-23T04:00:01+00:00",
      last_success_at: "2026-11-23T04:00:00.123456+00:00",
      retry_seconds: 0.75,
      events_read: 2,
    },
  };
  const value = normalise(raw);
  assert.equal(value.host.native_monitor.status, "waiting");
  assert.equal(value.host.native_monitor.retry_seconds, 0.75);
  assert.equal(
    value.host.native_monitor.last_success_at,
    raw.host.native_monitor.last_success_at,
  );
});

test("unreadable artefact preserves denial without claiming a hash", () => {
  const raw = fixture();
  Object.assign(raw.artefacts[0], {
    sha256: null,
    access_status: "unavailable",
    access_message: "현재 버전 확인 불가",
    downloadable: false,
    previewable: false,
  });
  const s = normalise(raw);
  assert.equal(s.artefacts[0].sha256, null);
  assert.equal(s.artefacts[0].access_status, "unavailable");
  assert.equal(s.artefacts[0].downloadable, false);
  assert.equal(s.tasks[0].status, "running");
});
for (const key of ["access_status", "access_message"])
  test(`reject malformed artefact access: ${key}`, () => {
    const raw = fixture();
    raw.artefacts[0][key] = { unsafe: true };
    assert.throws(() => normalise(raw), /스냅샷/);
  });
