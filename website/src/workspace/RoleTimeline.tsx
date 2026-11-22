import React from "react";
import {
  ROLE,
  active,
  completed,
  labels,
  commandsForTask,
  type Snapshot,
  type Task,
  type Selection,
} from "./model";
import "./role-timeline.css";
type Event = {
  task_id?: string;
  command_id?: string;
  at?: string;
  kind?: string;
  [key: string]: any;
};
type Interval = {
  id: string;
  task: Task;
  start: number;
  end: number;
  live: boolean;
  source: "execution" | "activity";
  state: string;
  track: number;
};
type Lane = {
  role: string;
  tasks: Task[];
  intervals: Interval[];
  tracks: number;
};
type Timeline = {
  lanes: Lane[];
  start: number | null;
  end: number | null;
  hasLive: boolean;
};
const stamp = (value: unknown): number | null => {
  if (typeof value !== "string" || !value.trim()) return null;
  const result = Date.parse(value);
  return Number.isFinite(result) && result > 0 ? result : null;
};
const firstStamp = (...values: unknown[]) =>
  values.map(stamp).find((v) => v !== null) ?? null;
const knownEnd = (start: number, ...values: unknown[]) =>
  values.map(stamp).find((v) => v !== null && v >= start) ?? start;
const working = (
  snapshot: Snapshot,
  task: Task,
  now: number,
  mode: string,
  connected: boolean,
) => {
  if (!connected || !active(task) || mode === "snapshot") return false;
  if (mode === "demo") return true;
  return (
    (snapshot.runners || []).some(
      (r: any) =>
        r.connected &&
        r.status === "running" &&
        r.task_id === task.id &&
        Number.isInteger(r.child_pid) &&
        r.child_pid > 0,
    ) ||
    snapshot.agents.some((a: any) => {
      const last = firstStamp(a.last_seen, a.last_activity_at);
      return (
        !a.superseded &&
        ["running", "active", "working", "preparing", "starting"].includes(
          a.status,
        ) &&
        (Boolean(a.native_agent_id && a.session_id) ||
          (last !== null && now >= last && now - last < 90000)) &&
        (a.task_id === task.id || (a.task_ids || []).includes(task.id))
      );
    })
  );
};
/** Actual observations only: planned start/end dates never become execution bars. */
export function buildRoleTimeline(
  snapshot: Snapshot,
  tasks: Task[],
  now: number,
  mode: string,
  connected: boolean,
): Timeline {
  const source = Array.isArray(snapshot.activity)
    ? snapshot.activity
    : Array.isArray(snapshot.activities)
      ? snapshot.activities
      : [];
  const events = source.filter((e: Event) => stamp(e.at) !== null) as Event[];
  const lanes = new Map<string, Lane>();
  for (const task of tasks) {
    const role = task.role || "unassigned";
    let lane = lanes.get(role);
    if (!lane) {
      lane = { role, tasks: [], intervals: [], tracks: 1 };
      lanes.set(role, lane);
    }
    lane.tasks.push(task);
    const currentCommands = commandsForTask(task, snapshot);
    const currentCommandIds = new Set(
      currentCommands.map((command: any) => command.id),
    );
    const taskEvents = events
      .filter(
        (e) =>
          e.task_id === task.id &&
          (!e.command_id || currentCommandIds.has(e.command_id)),
      )
      .sort((a, b) => stamp(a.at)! - stamp(b.at)!);
    const commands = currentCommands
      .filter((c: any) => stamp(c.started_at) !== null)
      .sort((a: any, b: any) => stamp(a.started_at)! - stamp(b.started_at)!);
    const isLive = working(snapshot, task, now, mode, connected);
    if (commands.length) {
      commands.forEach((c: any, index: number) => {
        const start = stamp(c.started_at)!;
        const related = taskEvents.filter(
          (e) =>
            (!e.command_id || e.command_id === c.id) && stamp(e.at)! >= start,
        );
        const live =
          isLive && c.status === "running" && index === commands.length - 1;
        const terminal = related.find((e) =>
          ["completed", "failed", "agent_completed", "agent_failed"].includes(
            e.kind || "",
          ),
        );
        const closed = ["completed", "failed"].includes(c.status);
        const end = live
          ? Math.max(start, now)
          : knownEnd(
              start,
              c.completed_at,
              c.failed_at,
              terminal?.at,
              closed && index === commands.length - 1
                ? task.attempt_finished_at
                : null,
              closed && index === commands.length - 1 ? task.actual_end : null,
              related.at(-1)?.at,
            );
        lane!.intervals.push({
          id: c.id || `${task.id}-${index}`,
          task,
          start,
          end,
          live,
          source: "execution",
          state:
            c.status === "completed"
              ? "done"
              : c.status === "failed"
                ? "failed"
                : live
                  ? "running"
                  : "unconfirmed",
          track: 0,
        });
      });
    } else {
      const reportedStart = firstStamp(
        task.native_started_at,
        task.attempt_started_at,
        task.actual_start,
      );
      const startEvent = taskEvents.find((e) =>
        ["started", "agent_started", "delegation_bound"].includes(e.kind || ""),
      );
      const start =
        reportedStart ?? firstStamp(startEvent?.at, taskEvents[0]?.at);
      if (start !== null) {
        const terminal = [...taskEvents]
          .reverse()
          .find((e) =>
            ["completed", "failed", "agent_completed", "agent_failed"].includes(
              e.kind || "",
            ),
          );
        const end = isLive
          ? Math.max(start, now)
          : knownEnd(
              start,
              task.native_finished_at,
              task.attempt_finished_at,
              !active(task) ? task.actual_end : null,
              terminal?.at,
              taskEvents.at(-1)?.at,
              task.last_activity_at,
            );
        lane.intervals.push({
          id: task.id,
          task,
          start,
          end,
          live: isLive,
          source:
            reportedStart !== null || startEvent ? "execution" : "activity",
          state: completed(task)
            ? "done"
            : task.status === "failed"
              ? "failed"
              : isLive
                ? "running"
                : "unconfirmed",
          track: 0,
        });
      }
    }
  }
  for (const lane of lanes.values()) {
    const tracks: number[] = [];
    lane.intervals
      .sort((a, b) => a.start - b.start)
      .forEach((i) => {
        let t = tracks.findIndex((end) => end <= i.start);
        if (t < 0) t = tracks.length;
        tracks[t] = i.end;
        i.track = t;
      });
    lane.tracks = Math.max(1, tracks.length);
  }
  const all = [...lanes.values()].flatMap((l) => l.intervals);
  if (!all.length)
    return {
      lanes: [...lanes.values()],
      start: null,
      end: null,
      hasLive: false,
    };
  const min = Math.min(...all.map((i) => i.start)),
    max = Math.max(...all.map((i) => i.end)),
    span = Math.max(max - min, 60000),
    pad = Math.min(Math.max(span * 0.035, 1000), 300000);
  return {
    lanes: [...lanes.values()],
    start: min - pad,
    end: Math.max(max + pad, min + 60000),
    hasLive: all.some((i) => i.live),
  };
}
const timeLabel = (at: number, span: number, withDate = false) =>
  new Date(at).toLocaleTimeString("ko-KR", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    ...(span < 240000 ? { second: "2-digit" } : {}),
    ...(withDate ? { month: "numeric", day: "numeric" } : {}),
  });
const fullTime = (at: number) =>
  new Date(at).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
export function RoleTimeline({
  snapshot,
  tasks,
  selection,
  select,
  now,
  mode,
  connected,
}: {
  snapshot: Snapshot;
  tasks: Task[];
  selection: Selection | null;
  select: (v: Selection) => void;
  now: number;
  mode: string;
  connected: boolean;
}) {
  const t = buildRoleTimeline(snapshot, tasks, now, mode, connected),
    start = t.start,
    end = t.end,
    span = start !== null && end !== null ? end - start : 1,
    hasTimes = start !== null && end !== null;
  const long =
    hasTimes &&
    (span > 43200000 ||
      new Date(start!).toDateString() !== new Date(end!).toDateString());
  const ticks = hasTimes
    ? Array.from({ length: 5 }, (_, i) => start! + (span * i) / 4)
    : [];
  const nowX = hasTimes ? ((now - start!) / span) * 100 : 0,
    showNow = t.hasLive && nowX >= 0 && nowX <= 100;
  return (
    <section className="role-timeline" aria-label="역할별 작업 흐름">
      <header className="role-timeline-heading">
        <h2>역할별 작업 흐름</h2>
        <span>
          {mode === "demo" ? "예시 실행 구간" : "실제 보고된 실행 구간"}
        </span>
      </header>
      {t.lanes.length ? (
        <div
          className="role-timeline-scroll"
          tabIndex={0}
          aria-label="역할별 실행 시간표, 좁은 화면에서는 가로 스크롤"
        >
          <div className="role-timeline-grid">
            <div className="role-timeline-axis">
              <span className="role-timeline-axis-label">담당 역할</span>
              <div className="role-timeline-axis-times">
                {ticks.map((tick, i) => (
                  <time
                    key={tick}
                    dateTime={new Date(tick).toISOString()}
                    title={fullTime(tick)}
                    style={{ left: `${i * 25}%` }}
                    className={
                      i === 0
                        ? "is-first"
                        : i === ticks.length - 1
                          ? "is-last"
                          : ""
                    }
                  >
                    {timeLabel(tick, span, Boolean(long))}
                  </time>
                ))}
                {!hasTimes && (
                  <span className="role-timeline-no-axis">
                    실행 시각이 보고되면 시간축이 표시됩니다.
                  </span>
                )}
              </div>
            </div>
            <div className="role-timeline-lanes">
              {t.lanes.map((l) => (
                <div
                  className="role-timeline-lane"
                  key={l.role}
                  style={{ minHeight: `${Math.max(46, l.tracks * 32 + 12)}px` }}
                >
                  <span
                    className="role-timeline-role"
                    title={ROLE[l.role] || l.role}
                  >
                    {ROLE[l.role] ||
                      (l.role === "unassigned" ? "담당 미정" : l.role)}
                  </span>
                  <div className="role-timeline-track">
                    {ticks.map((tick, i) => (
                      <i
                        className="role-timeline-gridline"
                        key={tick}
                        style={{ left: `${i * 25}%` }}
                        aria-hidden="true"
                      />
                    ))}
                    {l.intervals.map((i) => {
                      const left = ((i.start - start!) / span) * 100,
                        width = ((i.end - i.start) / span) * 100,
                        state =
                          i.state === "unconfirmed"
                            ? "마지막 보고 구간"
                            : labels[i.state] || i.state,
                        description = `${i.task.title}, ${ROLE[l.role] || l.role}, ${fullTime(i.start)}부터 ${fullTime(i.end)}${i.live ? " 현재 진행 중" : `, ${state}`}${i.source === "activity" ? ", 활동 보고 시각 기준" : ""}`;
                      return (
                        <button
                          key={i.id}
                          className={`role-task-bar state-${i.state} ${selection?.kind === "task" && selection.id === i.task.id ? "is-selected" : ""}`}
                          data-task-id={i.task.id}
                          data-start={new Date(i.start).toISOString()}
                          data-end={new Date(i.end).toISOString()}
                          data-live={i.live}
                          style={{
                            left: `${left}%`,
                            width: `${Math.max(width, 0.35)}%`,
                            top: `${i.track * 32 + 7}px`,
                          }}
                          title={description}
                          aria-label={description}
                          onClick={() =>
                            select({ kind: "task", id: i.task.id })
                          }
                        >
                          <span>{i.task.title}</span>
                          {i.live && (
                            <i className="role-task-live" aria-hidden="true" />
                          )}
                        </button>
                      );
                    })}
                    {!l.intervals.length && (
                      <span className="role-timeline-unreported">
                        {l.tasks.every(completed)
                          ? "완료 · 실행 시각 미보고"
                          : l.tasks.some(active)
                            ? "진행 보고 · 실행 시각 미보고"
                            : `${l.tasks.length}개 작업 대기 · 실행 시각 미정`}
                      </span>
                    )}
                  </div>
                </div>
              ))}
              {showNow && (
                <div className="role-timeline-now" aria-hidden="true">
                  <div style={{ left: `${nowX}%` }}>
                    <span>지금</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <p className="role-timeline-empty">
          작업을 연결하면 역할별 실행 흐름이 표시됩니다.
        </p>
      )}
      {hasTimes && (
        <p className="role-timeline-note">
          {showNow
            ? "실행 중인 역할의 실제 경과 시간을 표시합니다."
            : "마지막으로 보고된 실행 구간입니다."}
        </p>
      )}
    </section>
  );
}
