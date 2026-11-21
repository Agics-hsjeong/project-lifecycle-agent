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
    verificationOf({ ...task, verification: { status: "passed", checks: 1, source: "supervisor" } })
      .state,
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
  assert.deepEqual(verificationOf({ ...task, verification: { status: "passed", checks: 2, source: "host-reported" } }),
    { state: "host_reported", label: "호스트 검증 보고" });
  assert.deepEqual(verificationOf({ ...task, verification: { status: "passed", checks: 2 } }),
    { state: "unknown", label: "검증 출처 미보고" });
  assert.equal(verificationOf({ ...task, verification: { status: "passed", checks: 2, source: "untrusted" } }).state, "unknown");
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
