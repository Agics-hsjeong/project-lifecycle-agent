import {
  makeDemo,
  demoAction as decide,
  finishDemoRework,
  demoCode,
} from "./demo";
import type { Snapshot } from "./model";
export { makeDemo };
export function demoAction(
  s: Snapshot,
  gateId: string,
  decision: string,
  feedback: string,
  actor: string,
) {
  return decision === "complete_revision"
    ? finishDemoRework(s)
    : decide(s, gateId, decision, feedback, actor);
}
export function demoDiff(path: string) {
  return {
    path,
    baseline_available: true,
    binary: false,
    sha256: "demo-6f12e0a94be8",
    content: demoCode,
    diff:
      "--- a/" +
      path +
      "\n+++ b/" +
      path +
      "\n@@ -8,4 +8,6 @@\n       responses:\n         201:\n           description: 요청 등록 완료\n-        500:\n-          description: 오류\n+        403:\n+          description: 접근 권한 없음\n+        409:\n+          description: 이미 처리된 요청\n",
  };
}

// This clock advances only the explicitly labelled public demonstration.
export function tickDemo(snapshot: Snapshot): Snapshot {
  if (snapshot.mode !== "simulation") return snapshot;
  const at = new Date().toISOString();
  const tasks = snapshot.tasks.map((task) => ({ ...task }));
  const current = tasks.find((task) =>
    ["running", "doing"].includes(task.status),
  );
  if (!current) return { ...snapshot, captured_at: at };
  const activity = [...(snapshot.activity || [])];
  let seq = snapshot.activity_seq || activity.length;
  activity.push({
    id: `demo-activity-${++seq}`,
    seq,
    at,
    kind: "completed",
    role: current.role,
    provider: current.role === "database" ? "claude" : "codex",
    task_id: current.id,
    summary: `${current.title} 산출물과 검증 기록을 가상으로 완료했습니다.`,
  });
  current.status = "done";
  current.progress = 100;
  current.actual_end = at;
  current.latest_action =
    "가상 시나리오에서 산출물과 검증 기록을 완료했습니다.";
  const next = tasks.find(
    (task) =>
      ["todo", "blocked", "ready"].includes(task.status) &&
      task.depends_on.every((id) =>
        tasks.some((parent) => parent.id === id && parent.status === "done"),
      ),
  );
  if (next) {
    next.status = "running";
    next.actual_start = at;
    next.progress = null;
    next.latest_action = `${next.title}에 필요한 입력 파일을 확인합니다.`;
    activity.push({
      id: `demo-activity-${++seq}`,
      seq,
      at,
      kind: "handoff",
      role: current.role,
      peer_role: next.role,
      task_id: next.id,
      summary: `${current.title} 결과를 ${next.role} 역할의 다음 작업에 연결했습니다.`,
    });
    activity.push({
      id: `demo-activity-${++seq}`,
      seq,
      at,
      kind: "tool_started",
      role: next.role,
      provider: "codex",
      task_id: next.id,
      summary: next.latest_action,
    });
  }
  const roles = [
    ...new Set(
      tasks
        .filter((task) => task.status === "running")
        .map((task) => task.role),
    ),
  ];
  const agents = roles.map((role) => ({
    id: `demo-${role}`,
    role,
    provider: role === "database" ? "claude" : "codex",
    status: "running",
    last_seen: at,
    task_id: tasks.find(
      (task) => task.role === role && task.status === "running",
    )!.id,
    current_action: tasks.find(
      (task) => task.role === role && task.status === "running",
    )!.latest_action,
  }));
  return {
    ...snapshot,
    tasks,
    agents,
    activity: activity.slice(-100),
    activity_seq: seq,
    captured_at: at,
    revision: `demo-${seq}`,
  };
}
