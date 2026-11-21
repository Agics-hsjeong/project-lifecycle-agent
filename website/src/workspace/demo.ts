import { PHASES, type Snapshot } from "./model";
const definitions = [
  [
    "원본 자료 등록",
    "orchestrator",
    "discovery",
    [],
    "inputs/rfp/customer-portal.md",
  ],
  ["사업 목표와 성공 기준", "po", "discovery", [1], "docs/01-discovery.md"],
  ["요구사항 기준선", "spec", "discovery", [2], "registers/requirements.csv"],
  [
    "평가 항목과 제안 전략",
    "proposal-strategist",
    "proposal",
    [3],
    "docs/15-proposal-strategy.md",
  ],
  [
    "정성제안서 작성",
    "proposal-producer",
    "proposal",
    [4],
    "proposal/qualitative-proposal.md",
  ],
  [
    "발표자료와 발표 대본",
    "proposal-producer",
    "proposal",
    [4],
    "proposal/presentation.md",
  ],
  ["제안 제출 검토", "reviewer", "proposal", [5, 6], "docs/02-proposal.md"],
  ["착수와 범위 확정", "orchestrator", "kickoff", [7], "docs/03-kickoff.md"],
  ["기능 정책과 정보 구조", "spec", "planning", [8], "docs/04-planning.md"],
  ["시스템·API 계약", "architect", "planning", [9], "src/openapi.yaml"],
  ["데이터 모델 설계", "database", "planning", [9], "src/schema.sql"],
  [
    "디자인 시스템과 화면 설계",
    "design-system",
    "design",
    [9],
    "docs/06-design.md",
  ],
  ["프론트엔드 구현", "frontend", "implementation", [10, 12], "src/portal.tsx"],
  ["백엔드 구현", "backend", "implementation", [10, 11], "src/routes.py"],
  [
    "통합 테스트와 품질 검증",
    "qa",
    "verification",
    [13, 14],
    "docs/10-verification.md",
  ],
  [
    "보안과 코드 검토",
    "security",
    "verification",
    [13, 14],
    "reports/security.md",
  ],
  ["배포·복구 검증", "devops", "release", [15, 16], "docs/11-release.md"],
  [
    "인수와 사용자 매뉴얼",
    "technical-writer",
    "handover",
    [17],
    "docs/12-handover.md",
  ],
  ["운영과 변경 대응", "devops", "maintenance", [18], "docs/13-maintenance.md"],
] as const;
const id = (n: number) => `TASK-${String(n).padStart(3, "0")}`;
export const demoCode = `openapi: 3.1.0
info:
  title: 고객 포털 API
  version: 1.0.0
paths:
  /requests:
    post:
      summary: 서비스 요청 등록
      security:
        - bearerAuth: []
      responses:
        '201':
          description: 요청 등록 완료
        '403':
          description: 접근 권한 없음
`;
export function makeDemo(): Snapshot {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const day = (offset: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() + offset);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
  const tasks = definitions.map(([title, role, stage, deps, path], i) => ({
    id: id(i + 1),
    title,
    role,
    owner: role,
    stage,
    phase: PHASES.find((p) => p.stages.includes(stage))!.id,
    status: (i < 9
      ? "done"
      : i === 9
        ? "running"
        : i === 10 || i === 11
          ? "running"
          : i === 12 || i === 13
            ? "blocked"
            : "todo") as any,
    progress: i < 9 ? 100 : null,
    actual_start:
      i >= 9 && i <= 11
        ? new Date(Date.now() - (i - 8) * 41000).toISOString()
        : null,
    latest_action:
      i === 9
        ? "API 요청·응답 계약을 정리하고 있습니다."
        : i === 10
          ? "요구사항에 연결된 데이터 모델을 설계합니다."
          : i === 11
            ? "공통 컴포넌트와 화면 상태를 정의합니다."
            : null,
    requirement_ids: ["REQ-001"],
    depends_on: deps.map(id),
    artefact_ids: [`file:${path}`],
    start: day(Math.floor(i * 0.9) - 10),
    end: day(Math.floor(i * 0.9) - 8),
  }));
  return {
    schema_version: 1,
    mode: "simulation",
    revision: "demo-1",
    captured_at: new Date().toISOString(),
    project: {
      id: "demo-portal",
      name: "고객 포털 구축",
      current_stage: "planning",
      profile: "si",
      version: "2.0.0-beta.4",
    },
    phases: PHASES,
    tasks,
    artefacts: definitions.map(([title, , , , path]) => ({
      id: `file:${path}`,
      path,
      title,
      sha256: "demo-6f12e0a94be8",
    })),
    view_mode: "observe",
    activity_seq: 4,
    activity: [
      {
        id: "demo-event-1",
        seq: 1,
        at: new Date(Date.now() - 70000).toISOString(),
        task_id: id(9),
        role: "spec",
        provider: "codex",
        kind: "handoff",
        peer_role: "architect",
        summary: "기능 정책과 예외 시나리오를 시스템 설계 역할에 전달했습니다.",
      },
      {
        id: "demo-event-2",
        seq: 2,
        at: new Date(Date.now() - 26000).toISOString(),
        task_id: id(10),
        role: "architect",
        provider: "codex",
        kind: "tool_started",
        summary: "API 요청·응답 계약의 관련 파일을 읽고 있습니다.",
        paths: ["src/openapi.yaml"],
      },
      {
        id: "demo-event-3",
        seq: 3,
        at: new Date(Date.now() - 15000).toISOString(),
        task_id: id(11),
        role: "database",
        provider: "claude",
        kind: "file",
        summary: "요구사항에 연결된 데이터 모델을 작성했습니다.",
        paths: ["src/schema.sql"],
      },
      {
        id: "demo-event-4",
        seq: 4,
        at: new Date(Date.now() - 6000).toISOString(),
        task_id: id(12),
        role: "design-system",
        provider: "codex",
        kind: "action",
        summary: "공통 컴포넌트의 상태와 반응형 기준을 정리하고 있습니다.",
      },
    ],
    agents: [
      {
        id: "demo-architect",
        role: "architect",
        provider: "codex",
        status: "running",
        task_id: id(10),
        last_seen: new Date().toISOString(),
      },
      {
        id: "demo-database",
        provider: "claude",
        last_seen: new Date().toISOString(),
        role: "database",
        status: "running",
        task_id: id(11),
      },
      {
        id: "demo-design",
        provider: "codex",
        last_seen: new Date().toISOString(),
        role: "design-system",
        status: "running",
        task_id: id(12),
      },
    ],
    relations: [
      ...tasks.flatMap((t) =>
        t.depends_on.map((parent) => ({
          source: parent,
          target: t.id,
          type: "depends_on",
          evidence: "시뮬레이션 실행 계획",
        })),
      ),
      ...tasks.flatMap((t) =>
        t.artefact_ids.map((file) => ({
          source: t.id,
          target: file,
          type: "produces",
          evidence: "시뮬레이션 산출물",
        })),
      ),
    ],
    gates: [],
    logs: [
      {
        at: new Date().toISOString(),
        actor: "시뮬레이션",
        message:
          "이 시나리오의 작업·역할·행동은 화면 안내용 가상 데이터입니다.",
      },
    ],
    usage: { tokens: 142680, cost: 2.74, source: "시뮬레이션 예시" },
    capabilities: { approve: false, revision: false, host_connected: true },
    host: { connected: true },
  };
}
export function demoAction(
  s: Snapshot,
  gateId: string,
  decision: string,
  feedback: string,
  actor: string,
): Snapshot {
  const gate = s.gates.find((g) => g.id === gateId)!;
  let tasks = s.tasks.map((t) => ({ ...t }));
  let gates = s.gates.map((g) => ({ ...g }));
  const logs = [...s.logs];
  const now = new Date().toISOString();
  if (decision === "approve") {
    tasks = tasks.map((t) =>
      t.id === gate.task_id ? { ...t, status: "done", progress: 100 } : t,
    );
    gates = gates.map((g) =>
      g.id === gateId ? { ...g, status: "approved" } : g,
    );
    tasks = tasks.map((t) =>
      ["todo", "blocked"].includes(t.status) &&
      t.depends_on.length &&
      t.depends_on.every(
        (dep) => tasks.find((p) => p.id === dep)?.status === "done",
      )
        ? { ...t, status: "running" }
        : t,
    );
    logs.push({
      at: now,
      actor,
      message:
        "산출물 버전을 승인했습니다. 선행 조건을 충족한 시뮬레이션 작업을 시작합니다.",
    });
  } else {
    const original = tasks.find((t) => t.id === gate.task_id)!;
    const attempt = `${original.id}-R${s.logs.length}`;
    tasks = tasks.map((t) =>
      t.id === original.id ? { ...t, status: "blocked" } : t,
    );
    tasks.push({
      ...original,
      id: attempt,
      title: `${original.title} · 수정`,
      status: "running",
      progress: null,
      depends_on: [],
      revision_of: original.id,
    });
    gates = gates.map((g) =>
      g.id === gateId ? { ...g, status: "revision_requested" } : g,
    );
    logs.push({ at: now, actor, message: `수정 요청: ${feedback}` });
  }
  return {
    ...s,
    tasks,
    gates,
    logs,
    revision: `demo-${Date.now()}`,
    captured_at: now,
  };
}
export function finishDemoRework(s: Snapshot): Snapshot {
  let tasks = s.tasks.map((t) => ({ ...t }));
  const rework = tasks.find((t) => t.revision_of && t.status === "running");
  if (!rework) return s;
  tasks = tasks.map((t) =>
    t.id === rework.id
      ? { ...t, status: "done", progress: 100 }
      : t.id === rework.revision_of
        ? { ...t, status: "awaiting_approval" }
        : t,
  );
  const gate = s.gates.find((g) => g.task_id === rework.revision_of)!;
  const doneTasks = tasks.map((t) =>
    t.id === rework.id
      ? { ...t, status: "done" as any, progress: 100 }
      : t.id === rework.revision_of
        ? { ...t, status: "awaiting_approval" as any }
        : t,
  );
  return {
    ...s,
    tasks: doneTasks,
    revision: `demo-${Date.now()}`,
    gates: [
      ...s.gates,
      { ...gate, id: `${gate.id}-R${s.gates.length}`, status: "pending" },
    ],
    logs: [
      ...s.logs,
      {
        at: new Date().toISOString(),
        actor: "시뮬레이션",
        message: "수정 산출물이 제출되었습니다. 다시 검토해 주세요.",
      },
    ],
  };
}
