import React, { useMemo, useRef, useState } from "react";
import {
  PHASES,
  ROLE,
  active,
  executionLabel,
  completed,
  labels,
  commandsForTask,
  phaseOf,
  type Selection,
  type Snapshot,
  type Task,
} from "./model";
import { Icon } from "./WorkspacePrimitives";
import { buildRoleTimeline } from "./RoleTimeline";
import "./wbs-view.css";

export type WbsScale = "day" | "week" | "month";
const DAY = 86400000;
const KST = 9 * 3600000;
const finiteStamp = (value: unknown) => {
  if (typeof value !== "string" || !value.trim()) return null;
  const n = Date.parse(value);
  return Number.isFinite(n) ? n : null;
};
/** Calendar dates are Korea-local; axis numbers use UTC only as a stable calendar serial. */
export function wbsCalendarDay(value: unknown): number | null {
  if (typeof value !== "string") return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-").map(Number);
    const n = Date.UTC(y, m - 1, d);
    return new Date(n).toISOString().slice(0, 10) === value ? n : null;
  }
  const n = finiteStamp(value);
  return n === null ? null : Math.floor((n + KST) / DAY) * DAY;
}
export function wbsPlan(task: Task) {
  const start = wbsCalendarDay(task.start);
  const end = wbsCalendarDay(task.end || task.due_date);
  return start !== null && end !== null && end >= start
    ? {
        start,
        end: end + DAY,
        sourceStart: task.start!,
        sourceEnd: (task.end || task.due_date)!,
      }
    : null;
}
const monthStart = (n: number) => {
  const d = new Date(n);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
};
const addMonths = (n: number, count: number) => {
  const d = new Date(n);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + count, 1);
};
const weekStart = (n: number) => {
  const d = new Date(n);
  return Math.floor(n / DAY) * DAY - ((d.getUTCDay() + 6) % 7) * DAY;
};
export function createWbsAxis(
  values: number[],
  scale: WbsScale,
  now = Date.now(),
) {
  const known = values.filter(Number.isFinite);
  const today = Math.floor((now + KST) / DAY) * DAY;
  const earliest = known.length ? Math.min(...known) : today;
  const latest = known.length ? Math.max(...known) : today + 6 * DAY;
  let start: number, end: number, count: number;
  if (scale === "month") {
    start = addMonths(monthStart(earliest), -1);
    end = addMonths(monthStart(latest), 2);
    const a = new Date(start),
      b = new Date(end);
    count =
      (b.getUTCFullYear() - a.getUTCFullYear()) * 12 +
      b.getUTCMonth() -
      a.getUTCMonth();
  } else if (scale === "week") {
    start = weekStart(earliest) - 7 * DAY;
    end = weekStart(latest) + 14 * DAY;
    count = Math.round((end - start) / (7 * DAY));
  } else {
    start = Math.floor(earliest / DAY) * DAY - DAY;
    end = Math.floor(latest / DAY) * DAY + 2 * DAY;
    if (end - start < 7 * DAY) end = start + 7 * DAY;
    count = Math.round((end - start) / DAY);
  }
  const width = Math.max(
    640,
    Math.min(
      72000,
      count * (scale === "day" ? 78 : scale === "week" ? 156 : 216),
    ),
  );
  const stride = Math.max(1, Math.ceil(count / 120));
  const boundaries = [];
  for (let i = 0; i < count; i += stride) {
    const at =
      scale === "month"
        ? addMonths(start, i)
        : start + i * (scale === "week" ? 7 : 1) * DAY;
    const next =
      scale === "month"
        ? addMonths(start, Math.min(count, i + stride))
        : Math.min(end, at + stride * (scale === "week" ? 7 : 1) * DAY);
    boundaries.push({
      at,
      end: next,
      left: ((at - start) / (end - start)) * width,
      width: ((next - at) / (end - start)) * width,
    });
  }
  return {
    start,
    end,
    width,
    count,
    boundaries,
    today,
    showToday: today >= start && today < end,
    dayStride: stride,
  };
}
const shortDate = (n: number) => {
  const d = new Date(n);
  return `${d.getUTCMonth() + 1}.${d.getUTCDate()}`;
};
const axisLabel = (n: number, scale: WbsScale) => {
  const d = new Date(n);
  return scale === "month"
    ? `${d.getUTCFullYear()}.${String(d.getUTCMonth() + 1).padStart(2, "0")}`
    : shortDate(n);
};
const fullKst = (n: number) =>
  new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(n);
function actualIntervals(snapshot: Snapshot, tasks: Task[]) {
  const capture = finiteStamp(snapshot.captured_at);
  const clock = capture ?? Date.now();
  const mode = snapshot.mode || "snapshot";
  const fresh =
    capture !== null &&
    Date.now() - capture < 90000 &&
    capture - Date.now() < 60000;
  const connected =
    fresh &&
    (mode === "demo" ||
      Boolean(snapshot.host?.connected) ||
      (snapshot.runners || []).some((r: any) => r.connected));
  return buildRoleTimeline(
    snapshot,
    tasks,
    clock,
    mode,
    connected,
  ).lanes.flatMap((l) => l.intervals);
}
export function Wbs({
  snapshot,
  tasks,
  selection,
  onSelect,
  toolbar,
}: {
  snapshot: Snapshot;
  tasks: Task[];
  selection: Selection | null;
  onSelect: (value: Selection) => void;
  toolbar?: React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [scale, setScale] = useState<WbsScale>("day");
  const scroll = useRef<HTMLDivElement>(null);
  const intervals = useMemo(
    () => actualIntervals(snapshot, tasks),
    [snapshot, tasks],
  );
  const plans = useMemo(
    () => new Map(tasks.map((t) => [t.id, wbsPlan(t)])),
    [tasks],
  );
  const dates = [...plans.values()]
    .flatMap((p) => (p ? [p.start, p.end - 1] : []))
    .concat(intervals.flatMap((i) => [i.start + KST, i.end + KST]));
  const axis = createWbsAxis(dates, scale);
  const coordinate = (n: number) =>
    ((n - axis.start) / (axis.end - axis.start)) * axis.width;
  const plannedCount = tasks.filter((t) => plans.get(t.id)).length;
  const actualCount = new Set(intervals.map((i) => i.task.id)).size;
  const rows = PHASES.flatMap((phase) => {
    const children = tasks.filter((t) => phaseOf(t) === phase.id);
    return children.length
      ? [
          { phase, task: null as Task | null },
          ...(!collapsed.includes(phase.id)
            ? children.map((task) => ({ phase, task }))
            : []),
        ]
      : [];
  });
  const lastCommands = new Map(
    tasks.map((t) => [t.id, commandsForTask(t, snapshot).at(-1)]),
  );
  return (
    <section
      className="scene-panel wbs-scene"
      aria-label="예정 일정과 실제 실행 타임라인"
    >
      <div className="scene-toolbar">
        <div className="scene-meta">
          <span>
            <b>{tasks.length}</b>개 작업
          </span>
          <span>예정 {plannedCount}</span>
          <span>실행 기록 {actualCount}</span>
        </div>
        <div className="scene-controls">
          <div className="wbs-scale" role="group" aria-label="간트 시간 단위">
            {(
              [
                ["day", "일"],
                ["week", "주"],
                ["month", "월"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                aria-pressed={scale === value}
                onClick={() => setScale(value)}
              >
                {label}
              </button>
            ))}
          </div>
          {toolbar}
        </div>
      </div>
      <div
        className="scene-content wbs-sheet-scroll"
        ref={scroll}
        tabIndex={0}
        aria-label="일정 표, 가로 스크롤로 전체 기간 탐색"
      >
        <div
          className="wbs-sheet"
          style={{ "--wbs-chart": `${axis.width}px` } as React.CSSProperties}
        >
          <div className="wbs-sheet-head">
            <div className="wbs-sheet-left">
              <span>작업 / 담당 역할</span>
              <span>상태</span>
            </div>
            <div
              className="wbs-axis"
              aria-label={`${new Date(axis.start).getUTCFullYear()}년 일정 축`}
            >
              {axis.boundaries.map((tick) => (
                <div
                  className="wbs-axis-cell"
                  key={tick.at}
                  data-bucket-start={new Date(tick.at).toISOString()}
                  data-bucket-end={new Date(tick.end).toISOString()}
                  style={{ left: tick.left, width: tick.width }}
                >
                  <span>{axisLabel(tick.at, scale)}</span>
                  <small>
                    {scale === "week"
                      ? "시작 주"
                      : scale === "month"
                        ? "월"
                        : new Intl.DateTimeFormat("ko-KR", {
                            timeZone: "UTC",
                            weekday: "short",
                          }).format(tick.at)}
                  </small>
                </div>
              ))}
              {axis.showToday && (
                <span
                  className="wbs-today-label"
                  style={{ left: coordinate(axis.today) }}
                >
                  오늘
                </span>
              )}
            </div>
          </div>
          <div className="wbs-sheet-body">
            <div className="wbs-chart-grid" aria-hidden="true">
              {axis.boundaries.map((tick) => (
                <i key={tick.at} style={{ left: tick.left }} />
              ))}
              {axis.showToday && (
                <i
                  className="wbs-current-date"
                  style={{ left: coordinate(axis.today) }}
                />
              )}
            </div>
            {rows.map(({ phase, task }) => {
              const plan = task ? plans.get(task.id) : null;
              const actual = task
                ? intervals.filter((i) => i.task.id === task.id)
                : [];
              const latest = task ? lastCommands.get(task.id) : null;
              const failed = Boolean(
                task &&
                (task.status === "failed" ||
                  (task.status === "blocked" && latest?.status === "failed")),
              );
              const status = task
                ? executionLabel(task) ||
                  (task.status === "skipped" ? "적용 제외" : null) ||
                  (failed
                    ? "실패"
                    : active(task)
                      ? "진행 중"
                      : completed(task)
                        ? task.status === "skipped"
                          ? "적용 제외"
                          : "완료"
                        : ["awaiting_approval", "awaiting_decision"].includes(
                              task.status,
                            )
                          ? "판단 대기"
                          : task.status === "stale"
                            ? "재검토"
                            : "대기")
                : "";
              const warning = task
                ? task.waiting_reason ||
                  task.blocked_reason ||
                  (failed ? latest?.notes : null)
                : null;
              const phaseTasks = tasks.filter((t) => phaseOf(t) === phase.id);
              const phasePlans = phaseTasks
                .map((t) => plans.get(t.id))
                .filter(Boolean) as NonNullable<ReturnType<typeof wbsPlan>>[];
              const phaseStart = phasePlans.length
                ? Math.min(...phasePlans.map((p) => p.start))
                : null;
              const phaseEnd = phasePlans.length
                ? Math.max(...phasePlans.map((p) => p.end))
                : null;
              return (
                <div
                  key={task?.id || phase.id}
                  className={`wbs-sheet-row ${task ? "is-task" : "is-phase"} ${selection?.id === task?.id && task ? "is-selected" : ""}`}
                  data-task-id={task?.id}
                >
                  <div className="wbs-sheet-left">
                    {task ? (
                      <>
                        <button
                          className="wbs-task-label"
                          title={task.title}
                          onClick={() =>
                            onSelect({ kind: "task", id: task.id })
                          }
                        >
                          <strong>{task.title}</strong>
                          <small>
                            <span className="mono">{task.id}</span>
                            <span>
                              {ROLE[task.role] || task.role || "담당 미정"}
                            </span>
                          </small>
                        </button>
                        <span
                          className={`wbs-state-label ${failed ? "is-failed" : completed(task) ? "is-done" : active(task) ? "is-working" : ""}`}
                          title={warning || undefined}
                        >
                          <i aria-hidden="true" />
                          {status}
                        </span>
                      </>
                    ) : (
                      <button
                        className="wbs-phase-label"
                        aria-expanded={!collapsed.includes(phase.id)}
                        onClick={() =>
                          setCollapsed((value) =>
                            value.includes(phase.id)
                              ? value.filter((v) => v !== phase.id)
                              : [...value, phase.id],
                          )
                        }
                      >
                        <span
                          className={
                            collapsed.includes(phase.id) ? "" : "is-open"
                          }
                        >
                          <Icon name="chevron" size={13} />
                        </span>
                        <strong>{phase.title}</strong>
                        <small>{phaseTasks.length}</small>
                      </button>
                    )}
                  </div>
                  <div className="wbs-chart-row">
                    {!task && phaseStart !== null && phaseEnd !== null && (
                      <i
                        className="wbs-phase-range"
                        style={{
                          left: coordinate(phaseStart),
                          width: Math.max(
                            2,
                            coordinate(phaseEnd) - coordinate(phaseStart),
                          ),
                        }}
                        title="등록된 하위 작업의 예정 범위"
                      />
                    )}
                    {task && plan && (
                      <button
                        className={`wbs-plan-bar ${failed ? "has-issue" : ""}`}
                        data-task-id={task.id}
                        data-kind="planned"
                        data-start={new Date(plan.start - KST).toISOString()}
                        data-end={new Date(plan.end - KST).toISOString()}
                        style={{
                          left: coordinate(plan.start),
                          width: Math.max(
                            4,
                            coordinate(plan.end) - coordinate(plan.start),
                          ),
                        }}
                        onClick={() => onSelect({ kind: "task", id: task.id })}
                        aria-label={`${task.title}, ${shortDate(plan.start)} – ${shortDate(plan.end - DAY)}, 예정 ${plan.sourceStart}부터 ${plan.sourceEnd}`}
                        title={`${task.title} · 예정 ${plan.sourceStart}–${plan.sourceEnd}`}
                      >
                        <span>
                          {coordinate(plan.end) - coordinate(plan.start) > 88
                            ? `${shortDate(plan.start)} – ${shortDate(plan.end - DAY)}`
                            : ""}
                        </span>
                      </button>
                    )}
                    {task && !plan && (
                      <span className="wbs-plan-missing">
                        {task.start || task.end || task.due_date
                          ? "일정 확인 필요"
                          : "예정 일정 미정"}
                      </span>
                    )}
                    {actual.map((i) => {
                      const left = coordinate(i.start + KST),
                        width = Math.max(3, coordinate(i.end + KST) - left);
                      const text = `${task!.title}, 실제 실행 ${fullKst(i.start)}부터 ${fullKst(i.end)}${i.live ? ", 마지막 수신 시점까지 진행" : i.state === "failed" ? ", 실패" : i.state === "unconfirmed" ? ", 마지막 보고 구간" : ", 종료"}${i.source === "activity" ? ", 활동 보고 기준" : ""}`;
                      return (
                        <button
                          key={i.id}
                          className={`wbs-actual-bar ${i.state === "failed" ? "is-failed" : ""} ${i.state === "unconfirmed" ? "is-unconfirmed" : ""}`}
                          data-task-id={task!.id}
                          data-kind="actual"
                          data-start={new Date(i.start).toISOString()}
                          data-end={new Date(i.end).toISOString()}
                          style={{
                            left: left - 8,
                            width: Math.max(24, width + 16),
                          }}
                          aria-label={text}
                          title={text}
                          onClick={() =>
                            onSelect({ kind: "task", id: task!.id })
                          }
                        >
                          <i style={{ width }} aria-hidden="true" />
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            {!rows.length && (
              <div className="wbs-no-records">표시할 작업이 없습니다.</div>
            )}
          </div>
        </div>
      </div>
      <footer className="scene-footer wbs-scene-footer">
        <span className="wbs-key">
          <i className="is-plan" />
          예정 일정
        </span>
        <span className="wbs-key">
          <i className="is-actual" />
          실제 실행
        </span>
        <span>
          {shortDate(axis.start)} – {shortDate(axis.end - DAY)} · 한국 시간
        </span>
        <span className="wbs-footer-note">
          미등록 일정은 추정하지 않습니다.
        </span>
      </footer>
    </section>
  );
}
