import React, { useEffect, useMemo, useState } from "react";
import {
  ROLE,
  active,
  completed,
  excluded,
  settled,
  type Snapshot,
  type Task,
  type Selection,
} from "./model";
import { Icon } from "./Views";
import { RoleTimeline } from "./RoleTimeline";
import { hasIssue, waiting } from "./model";
import { VerificationBadge } from "./VerificationBadge";
import "./observer-b.css";

export type Activity = {
  seq?: number;
  id?: string;
  event_id?: string;
  at?: string;
  task_id?: string;
  agent_id?: string;
  role?: string;
  provider?: string;
  kind?: string;
  summary?: string;
  path?: string;
  paths?: string[];
  peer_role?: string;
  [key: string]: any;
};
export const activityOf = (snapshot: Snapshot): Activity[] =>
  (Array.isArray(snapshot.activity)
    ? snapshot.activity
    : Array.isArray(snapshot.activities)
      ? snapshot.activities
      : []
  )
    .filter((event: any) => event && typeof event.summary === "string")
    .slice()
    .sort(
      (a: Activity, b: Activity) =>
        (Date.parse(a.at || "") || 0) - (Date.parse(b.at || "") || 0),
    );
export function ageLabel(at: string | undefined, now: number) {
  const date = Date.parse(at || "");
  if (!Number.isFinite(date)) return "시각 미보고";
  const seconds = Math.max(0, Math.floor((now - date) / 1000));
  if (seconds < 60) return `${seconds}초 전`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  return new Date(date).toLocaleDateString("ko-KR", {
    month: "short",
    day: "numeric",
  });
}
export function freshAgent(agent: any, now: number) {
  const last = Date.parse(agent.last_seen || agent.last_activity_at || "");
  return (
    ["running", "active", "working"].includes(agent.status) &&
    Number.isFinite(last) &&
    now - last < 90000
  );
}
export function taskIsLive(
  snapshot: Snapshot,
  task: Task,
  now: number,
  mode: string,
) {
  if (!active(task) || mode === "snapshot") return false;
  if (mode === "demo") return true;
  return (
    (snapshot.runners || []).some(
      (runner: any) =>
        runner.connected &&
        runner.status === "running" &&
        Number.isInteger(runner.child_pid) &&
        runner.child_pid > 0 &&
        runner.task_id === task.id,
    ) ||
    snapshot.agents.some(
      (agent) =>
        freshAgent(agent, now) &&
        (agent.task_id === task.id || (agent.task_ids || []).includes(task.id)),
    )
  );
}
export function durationLabel(task: Task, now: number) {
  const hasAttempt = Boolean(task.attempt_started_at);
  const start = Date.parse(
    (hasAttempt ? task.attempt_started_at : task.actual_start) || "",
  );
  const finish = hasAttempt ? task.attempt_finished_at : task.actual_end;
  let end = Date.parse(finish || "");
  if (!Number.isFinite(end) && !active(task)) {
    const terminal = [...(task.activity || [])]
      .reverse()
      .find((event: Activity) =>
        ["completed", "failed"].includes(event.kind || ""),
      );
    end = Date.parse(terminal?.at || "");
  }
  let seconds: number | undefined;
  if (Number.isFinite(start) && Number.isFinite(end) && end >= start)
    seconds = (end - start) / 1000;
  else if (active(task) && Number.isFinite(start) && now >= start)
    seconds = (now - start) / 1000;
  else if (
    typeof task.elapsed_seconds === "number" &&
    Number.isFinite(task.elapsed_seconds)
  )
    seconds = task.elapsed_seconds;
  if (typeof seconds !== "number" || !Number.isFinite(seconds))
    return Number.isFinite(start) ? "종료 시각 미보고" : "시작 시각 미보고";
  seconds = Math.max(0, Math.floor(seconds));
  if (seconds < 60) return `${seconds}초`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
  return `${Math.floor(seconds / 3600)}시간 ${Math.floor((seconds % 3600) / 60)}분`;
}
const kindLabel: Record<string, string> = {
  action: "작업",
  tool_started: "도구 실행",
  tool_completed: "도구 결과",
  handoff: "역할 전달",
  repair_requested: "수정 작업 전달",
  repair_started: "수정 시작",
  review_passed: "AI 검토 통과",
  review_failed: "AI 검토 보완",
  tool_finished: "도구 결과",
  attention: "에이전트 확인 필요",
  review: "AI 검토",
  test: "검증",
  test_started: "검증 시작",
  verification_started: "검증 시작",
  verification_passed: "검증 통과",
  verification_failed: "검증 실패",
  failed: "실행 실패",
  test_passed: "검증 통과",
  test_failed: "검증 실패",
  error: "확인 필요",
  started: "시작",
  completed: "완료",
  file: "파일 변경",
  file_changed: "파일 변경",
  info: "기록",
  waiting: "대기",
};
export function ActivityFeed({
  events,
  now,
  select,
  compact = false,
}: {
  events: Activity[];
  now: number;
  select?: (value: Selection) => void;
  compact?: boolean;
}) {
  return events.length ? (
    <ol className={`activity-feed ${compact ? "is-compact" : ""}`}>
      {events
        .slice(-(compact ? 40 : 80))
        .reverse()
        .map((event, index) => (
          <li
            key={event.event_id || event.id || `${event.seq}-${index}`}
            className={`event-kind-${event.kind || "info"}`}
          >
            <div className="event-mark" aria-hidden="true">
              <Icon
                name={
                  event.kind === "handoff"
                    ? "map"
                    : event.kind?.includes("test") ||
                        event.kind?.includes("verification")
                      ? "shield"
                      : event.kind === "completed"
                        ? "check"
                        : "log"
                }
                size={14}
              />
            </div>
            <div className="event-body">
              <div className="event-meta">
                <strong>
                  {ROLE[event.role || ""] ||
                    event.role ||
                    event.provider ||
                    "실행 기록"}
                  {event.peer_role && (
                    <span>
                      {" "}
                      · {ROLE[event.peer_role] || event.peer_role}
                      {["handoff", "repair_requested"].includes(
                        event.kind || "",
                      )
                        ? "에게 전달"
                        : (event.kind || "").includes("review")
                          ? " 검토"
                          : " 관련"}
                    </span>
                  )}
                </strong>
                <time dateTime={event.at} title={event.at}>
                  {ageLabel(event.at, now)}
                </time>
              </div>
              <p>{event.summary}</p>
              <div className="event-foot">
                <span>
                  {kindLabel[event.kind || ""] || event.kind || "기록"}
                </span>
                {event.task_id &&
                  (select ? (
                    <button
                      onClick={() =>
                        select({ kind: "task", id: event.task_id! })
                      }
                    >
                      {event.task_id}
                    </button>
                  ) : (
                    <span className="mono">{event.task_id}</span>
                  ))}
                {Boolean(event.path || event.paths?.length) && (
                  <span className="event-path mono">
                    {event.path || event.paths?.join(" · ")}
                  </span>
                )}
              </div>
            </div>
          </li>
        ))}
    </ol>
  ) : (
    <div className="observer-empty">
      <Icon name="log" size={24} />
      <strong>아직 전달된 활동이 없습니다</strong>
      <p>
        코딩 에이전트가 보고한 행동과 역할 간 전달을 이곳에 표시합니다. 기록이
        없는 작업을 실행 중으로 꾸미지 않습니다.
      </p>
    </div>
  );
}
function taskNote(task: Task, snapshot: Snapshot, latest?: Activity) {
  if (latest && active(task)) return latest.summary;
  if (active(task))
    return (
      task.latest_action ||
      "작업 프로세스가 실행 중입니다. 다음 행동 보고를 기다립니다."
    );
  if (excluded(task))
    return (
      task.exclusion_reason ||
      task.latest_action ||
      "적용 범위에서 제외한 작업입니다. 완료율에 포함하지 않습니다."
    );
  if (completed(task))
    return (
      task.latest_action ||
      "파일 생성·작업 완료 기록입니다. 검증 상태를 별도로 확인하세요."
    );
  if (taskHasIssue(task, snapshot))
    return (
      task.waiting_reason ||
      task.latest_action ||
      task.blocked_reason ||
      currentCommand(task, snapshot)?.notes ||
      "실행 중 문제가 발견되었습니다. 에이전트의 진단·수정 기록을 확인하세요."
    );
  const pending = task.depends_on
    .map((id) => snapshot.tasks.find((t) => t.id === id))
    .filter((t) => t && !settled(t!));
  if (pending.length)
    return `${pending.map((t) => t!.title).join(" · ")} 완료 후 시작`;
  if (["awaiting_approval", "awaiting_decision"].includes(task.status))
    return "에이전트 채팅에서 필요한 판단을 확인할 수 있습니다.";
  return (
    task.waiting_reason ||
    task.blocked_reason ||
    task.latest_action ||
    "선행 조건이 준비되면 담당 역할이 이어서 처리합니다."
  );
}
function taskFile(task: Task, snapshot: Snapshot, events: Activity[]) {
  const recent = events
    .filter((event) => event.task_id === task.id)
    .flatMap((event) => [event.path, ...(event.paths || [])].filter(Boolean))
    .filter((path) => !String(path).startsWith("reports/runner/"));
  return (
    recent.at(-1) ||
    task.expected_outputs?.[0] ||
    task.evidence_refs?.find(
      (path: string) => !path.startsWith("reports/runner/"),
    ) ||
    task.artefact_ids
      .map((id) => snapshot.artefacts.find((file) => file.id === id)?.path)
      .find(Boolean) ||
    ""
  );
}
function taskProvider(task: Task, snapshot: Snapshot, events: Activity[]) {
  const latest = events
    .filter((event) => event.task_id === task.id && event.provider)
    .at(-1);
  const command = (snapshot.commands || [])
    .filter((item: any) => item.task_id === task.id && item.agent_id)
    .at(-1);
  const identifier = (value: string | undefined) =>
    (value || "").replace(/^agent:/, "");
  const agent =
    snapshot.agents.find(
      (item) => command && identifier(item.id) === identifier(command.agent_id),
    ) ||
    [...snapshot.agents]
      .reverse()
      .find(
        (item) =>
          item.task_id === task.id || (item.task_ids || []).includes(task.id),
      );
  const provider = latest?.provider || agent?.provider || task.provider;
  return provider === "codex"
    ? "Codex"
    : provider === "claude"
      ? "Claude Code"
      : typeof provider === "string"
        ? provider
        : "호스트 미보고";
}
function currentCommand(task: Task, snapshot: Snapshot) {
  return (snapshot.commands || [])
    .filter((command: any) => command.task_id === task.id)
    .at(-1);
}
function taskHasIssue(task: Task, snapshot: Snapshot) {
  return hasIssue(task, snapshot);
}

function statusText(task: Task, live: boolean, snapshot: Snapshot) {
  if (active(task)) return live ? "진행 중" : "활동 확인 필요";
  if (task.status !== "stale" && taskHasIssue(task, snapshot))
    return "실행 실패";
  if (task.status === "stale") return "재검증 필요";
  if (task.status === "skipped") return "적용 제외";
  if (completed(task)) return "완료";
  if (["awaiting_approval", "awaiting_decision"].includes(task.status))
    return "판단 대기";
  return "대기";
}
function roleIcon(role: string) {
  if (["qa", "reviewer", "security"].includes(role)) return "shield";
  if (["architect", "database", "devops"].includes(role)) return "workflow";
  return "file";
}
export function Observer({
  snapshot,
  tasks,
  selection,
  select,
  now,
  mode,
  connected,
  toolbar,
}: {
  snapshot: Snapshot;
  tasks: Task[];
  selection: Selection | null;
  select: (value: Selection) => void;
  now: number;
  mode: string;
  connected: boolean;
  toolbar?: React.ReactNode;
}) {
  const events = useMemo(() => activityOf(snapshot), [snapshot]);
  const [filter, setFilter] = useState("all");
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [allActivity, setAllActivity] = useState(false);
  useEffect(() => {
    if (
      selection?.kind === "task" &&
      tasks.some((task) => task.id === selection.id)
    )
      setFocusedId(selection.id);
  }, [selection?.id, selection?.kind]);
  const groups = [
    { id: "running", title: "진행 중", items: tasks.filter(active) },
    {
      id: "waiting",
      title: "대기",
      items: tasks.filter((task) => waiting(task, snapshot)),
    },
    {
      id: "attention",
      title: "확인 필요",
      items: tasks.filter((task) => hasIssue(task, snapshot)),
    },
    { id: "done", title: "완료", items: tasks.filter(completed) },
    { id: "excluded", title: "적용 제외", items: tasks.filter(excluded) },
  ];
  const shownGroups =
    filter === "all" ? groups : groups.filter((group) => group.id === filter);
  const visible = shownGroups.flatMap((group) => group.items);
  const recentTask = [...events]
    .reverse()
    .map((event) => tasks.find((task) => task.id === event.task_id))
    .find(Boolean);
  const focused =
    tasks.find((task) => task.id === focusedId) ||
    tasks.find(active) ||
    recentTask ||
    tasks[0];
  const focusTask = (task: Task, inspect = false) => {
    setFocusedId(task.id);
    setAllActivity(false);
    if (
      inspect ||
      (typeof window !== "undefined" &&
        window.matchMedia("(max-width:799px)").matches)
    )
      select({ kind: "task", id: task.id });
  };
  const focusedEvents = focused
    ? events.filter((event) => event.task_id === focused.id)
    : [];
  const focusedLatest = focusedEvents.at(-1);
  const focusedLive = Boolean(
    focused && connected && taskIsLive(snapshot, focused, now, mode),
  );
  const focusedFile = focused ? taskFile(focused, snapshot, events) : "";
  const focusedArtifact = snapshot.artefacts.find(
    (file) => file.path === focusedFile,
  );
  const filteredEvents = events.filter(
    (event) =>
      !event.task_id || tasks.some((task) => task.id === event.task_id),
  );
  const filters = [
    { id: "all", title: "전체", count: tasks.length },
    ...groups.map((group) => ({
      id: group.id,
      title: group.title,
      count: group.items.length,
    })),
  ];
  const contextEvents = allActivity ? filteredEvents : focusedEvents;
  return (
    <div className="b-observer">
      <div className="b-workspace-main">
        {!connected && mode !== "snapshot" && (
          <div className="b-connection-note" role="status">
            <Icon name="info" size={16} /> 연결을 확인하고 있습니다. 마지막
            보고된 작업과 활동을 표시합니다.
          </div>
        )}
        <section className="b-task-section" aria-labelledby="b-task-title">
          <header className="b-section-header">
            <h2 id="b-task-title">작업</h2>
            <div
              className="b-state-filters"
              role="group"
              aria-label="작업 상태"
            >
              {filters.map((item) => (
                <button
                  key={item.id}
                  aria-pressed={filter === item.id}
                  onClick={() => setFilter(item.id)}
                >
                  <span>{item.title}</span>
                  <b>{item.count}</b>
                </button>
              ))}
            </div>
            {toolbar && <div className="b-table-tools">{toolbar}</div>}
          </header>
          <div className="b-task-table-wrap">
            <table className="b-task-table" aria-label="역할별 작업 목록">
              <colgroup>
                <col className="b-col-task" />
                <col className="b-col-role" />
                <col className="b-col-action" />
                <col className="b-col-time" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">작업</th>
                  <th scope="col">담당</th>
                  <th scope="col">현재 상태</th>
                  <th scope="col">경과</th>
                </tr>
              </thead>
              {shownGroups.map((group) => (
                <tbody key={group.id} className={`b-group b-group-${group.id}`}>
                  {group.items.length > 0 && (
                    <tr className="b-group-title">
                      <th scope="rowgroup" colSpan={4}>
                        {group.title}
                        <span>{group.items.length}</span>
                      </th>
                    </tr>
                  )}
                  {group.items.map((task) => {
                    const latest = events
                      .filter((event) => event.task_id === task.id)
                      .at(-1);
                    const live =
                      connected && taskIsLive(snapshot, task, now, mode);
                    const file = taskFile(task, snapshot, events);
                    const status = statusText(task, live, snapshot);
                    const note = taskNote(task, snapshot, latest);
                    const isSelected = focused?.id === task.id;
                    const issue = taskHasIssue(task, snapshot);
                    return (
                      <tr
                        key={task.id}
                        data-task-id={task.id}
                        className={`b-task-row ${isSelected ? "is-selected" : ""} ${live ? "is-live" : ""} ${issue ? "has-issue" : ""}`}
                        onClick={() => focusTask(task)}
                      >
                        <td className="b-cell-task">
                          <button
                            className="b-task-open"
                            onClick={(event) => {
                              event.stopPropagation();
                              focusTask(task);
                            }}
                            aria-pressed={isSelected}
                          >
                            <span
                              className={`b-task-glyph ${completed(task) ? "is-complete" : ""}`}
                            >
                              <Icon
                                name={
                                  completed(task)
                                    ? "check"
                                    : roleIcon(task.role)
                                }
                                size={17}
                              />
                            </span>
                            <span className="b-task-heading">
                              <strong>{task.title}</strong>
                              <small>
                                <span className="mono">{task.id}</span>
                                {file && (
                                  <span className="b-row-file" title={file}>
                                    {file}
                                  </span>
                                )}
                              </small>
                            </span>
                          </button>
                        </td>
                        <td className="b-cell-role" data-label="담당">
                          <strong>
                            {ROLE[task.role] || task.role || "담당 미정"}
                          </strong>
                          <small>{taskProvider(task, snapshot, events)}</small>
                        </td>
                        <td className="b-cell-action" data-label="상태">
                          <span
                            className={`b-row-state ${live ? "is-live" : completed(task) ? "is-complete" : issue ? "is-issue" : ""}`}
                          >
                            <i aria-hidden="true" />
                            <span>{status}</span>
                          </span>
                          <VerificationBadge task={task} />
                          <small title={note}>{note}</small>
                        </td>
                        <td className="b-cell-time mono" data-label="경과">
                          {active(task) && !live
                            ? "확인 필요"
                            : !active(task) && !completed(task)
                              ? "—"
                              : durationLabel(task, now) === "시작 시각 미보고"
                                ? "—"
                                : durationLabel(task, now)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              ))}
            </table>
            {visible.length === 0 && (
              <div className="b-table-empty">
                <Icon name="workflow" size={25} />
                <strong>
                  {tasks.length
                    ? "이 상태의 작업이 없습니다"
                    : "표시할 작업이 없습니다"}
                </strong>
                <p>
                  {tasks.length
                    ? "다른 상태를 선택하면 작업 목록을 확인할 수 있습니다."
                    : "등록된 작업과 역할별 실행 기록이 이곳에 표시됩니다."}
                </p>
                {tasks.length > 0 && (
                  <button onClick={() => setFilter("all")}>
                    전체 작업 보기
                  </button>
                )}
              </div>
            )}
          </div>
        </section>
        <RoleTimeline
          snapshot={snapshot}
          tasks={tasks}
          selection={focused ? { kind: "task", id: focused.id } : selection}
          select={(value) => {
            const task = tasks.find((item) => item.id === value.id);
            if (value.kind === "task" && task) focusTask(task);
            else select(value);
          }}
          now={now}
          mode={mode}
          connected={connected}
        />
      </div>
      <aside className="b-context" aria-label="선택한 작업과 최근 활동">
        {focused ? (
          <>
            <section
              className="b-focused-task"
              aria-label="현재 작업 상세 요약"
            >
              <div className="b-context-overline">
                <span>{focused.id}</span>
                <span className={focusedLive ? "is-live" : ""}>
                  {statusText(focused, focusedLive, snapshot)}
                </span>
              </div>
              <h2>{focused.title}</h2>
              <p className="b-focused-role">
                <Icon name={roleIcon(focused.role)} size={16} />
                {ROLE[focused.role] || focused.role || "담당 미정"}
                <span>·</span>
                {taskProvider(focused, snapshot, events)}
              </p>
              {focusedFile &&
                (focusedArtifact ? (
                  <button
                    className="b-focused-file mono"
                    onClick={() =>
                      select({ kind: "artefact", id: focusedArtifact.id })
                    }
                  >
                    <Icon name="file" size={17} />
                    <span>{focusedFile}</span>
                    <Icon name="expand" size={15} />
                  </button>
                ) : (
                  <div className="b-focused-file mono">
                    <Icon name="file" size={17} />
                    <span>{focusedFile}</span>
                  </div>
                ))}
              <div
                className={`b-current-action ${focusedLive ? "is-live" : ""}`}
              >
                <i aria-hidden="true" />
                <p>{taskNote(focused, snapshot, focusedLatest)}</p>
              </div>
              <div className="b-focused-time">
                <span>
                  {active(focused) && !focusedLive
                    ? "마지막 활동"
                    : completed(focused)
                      ? "실제 경과"
                      : "진행 시간"}
                </span>
                <strong>
                  {active(focused) && !focusedLive
                    ? focusedLatest?.at
                      ? ageLabel(focusedLatest.at, now)
                      : "보고 없음"
                    : durationLabel(focused, now)}
                </strong>
              </div>
              {focusedLive && (
                <div className="b-working-dots" aria-label="실제 작업 진행 중">
                  <i />
                  <i />
                  <i />
                </div>
              )}
              <button
                className="b-inspect-link"
                onClick={() => focusTask(focused, true)}
              >
                작업 상세 보기
                <Icon name="expand" size={14} />
              </button>
            </section>
            <section
              className="b-recent-activity"
              aria-labelledby="b-recent-title"
            >
              <header>
                <h2 id="b-recent-title">최근 활동</h2>
                <button
                  aria-pressed={allActivity}
                  onClick={() => setAllActivity((value) => !value)}
                >
                  {allActivity ? "선택 작업" : "전체 활동"}
                </button>
              </header>
              <ActivityFeed
                events={contextEvents}
                now={now}
                select={(value) => {
                  const task = tasks.find((item) => item.id === value.id);
                  if (value.kind === "task" && task) focusTask(task);
                  else select(value);
                }}
                compact
              />
            </section>
          </>
        ) : (
          <div className="b-context-empty">
            <Icon name="agent" size={28} />
            <h2>작업을 선택하세요</h2>
            <p>
              담당 역할과 현재 행동, 관련 파일과 실제 활동을 확인할 수 있습니다.
            </p>
          </div>
        )}
      </aside>
    </div>
  );
}
