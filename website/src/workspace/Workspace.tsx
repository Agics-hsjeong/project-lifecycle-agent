import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  PHASES,
  ROLE,
  labels,
  phaseOf,
  completed,
  excluded,
  active,
  normalise,
  type Snapshot,
  type Task,
  type Selection,
} from "./model";
import { makeDemo, tickDemo, demoDiff } from "./simulation";
import { LatestRequest, isSnapshotBusy } from "./request-state";
import { WorkspaceBoundary } from "./WorkspaceBoundary";
import {
  taskSummary,
  hasIssue,
  waiting,
  usageCoverage,
  currentTasks,
  historyTasks,
  nativeWorkers,
  executionLabel,
} from "./model";
import { VerificationBadge } from "./VerificationBadge";
import { ArtefactDelivery } from "./ArtefactDelivery";
import { RunHistory } from "./RunHistory";
import "./quality-observer.css";
import {
  Observer,
  ActivityFeed,
  activityOf,
  taskIsLive,
  freshAgent,
} from "./Observer";
import {
  Workflow,
  KnowledgeMap,
  Wbs,
  TaskList,
  Icon,
  StatusBadge,
  Empty,
} from "./Views";
const tabs = [
  { id: "activity", label: "실행 현황", icon: "agent" },
  { id: "workflow", label: "워크플로우", icon: "workflow" },
  { id: "map", label: "작업 지도", icon: "map" },
  { id: "wbs", label: "타임라인", icon: "wbs" },
  { id: "artifacts", label: "산출물", icon: "file" },
];
const inspectTabs = [
  ["activity", "실행 활동"],
  ["diff", "파일·Diff"],
  ["links", "연결 정보"],
  ["schedule", "일정·리소스"],
  ["logs", "프로젝트 기록"],
  ["guidelines", "관찰 안내"],
];
const RECORDS_BUSY_NOTICE =
  "프로젝트 원장을 기록 중입니다. 마지막 수신 상태를 유지하며 다음 조회에서 최신 기록을 확인합니다.";
const prettyTime = (time: string) => {
  const d = new Date(time);
  return Number.isNaN(d.getTime())
    ? "시각 미상"
    : d.toLocaleTimeString("ko-KR", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
};
export default function Workspace(props: { base: string; version: string }) {
  return (
    <WorkspaceBoundary>
      <WorkspaceContent {...props} />
    </WorkspaceBoundary>
  );
}
function WorkspaceContent({
  base,
  version,
}: {
  base: string;
  version: string;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [mode, setMode] = useState<"demo" | "local" | "snapshot">("demo");
  const [tab, setTab] = useState("activity");
  const [workScope, setWorkScope] = useState<"current" | "history">("current");
  const [navOpen, setNavOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [demoPlaying, setDemoPlaying] = useState(true);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [inspectTab, setInspectTab] = useState("activity");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [phaseFilter, setPhaseFilter] = useState("all");
  const [roleFilter, setRoleFilter] = useState("all");
  const [focusTick, setFocusTick] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [connected, setConnected] = useState(false);
  const [diff, setDiff] = useState<any>(null);
  const [diffError, setDiffError] = useState("");
  const [diffBusy, setDiffBusy] = useState(false);
  const [diffRetry, setDiffRetry] = useState(0);
  const [filePath, setFilePath] = useState("");
  const [wideDiff, setWideDiff] = useState(false);
  const [mobileGraph, setMobileGraph] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [inspectorWidth, setInspectorWidth] = useState(384);
  const [connectionMode, setConnectionMode] = useState("");
  const tokenRef = useRef("");
  const importRef = useRef<HTMLInputElement>(null);
  const selectionOpener = useRef<HTMLElement | null>(null);
  const snapshotRef = useRef<Snapshot | null>(null);
  const modeRef = useRef("demo");
  const snapshotRequest = useRef(new LatestRequest());
  const diffRequest = useRef(new LatestRequest());
  const disposed = useRef(false);
  snapshotRef.current = snapshot;
  modeRef.current = mode;
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (mode !== "demo" || !demoPlaying) return;
    const timer = setInterval(
      () => setSnapshot((value) => (value ? tickDemo(value) : value)),
      6500,
    );
    return () => clearInterval(timer);
  }, [mode, demoPlaying]);
  useEffect(() => {
    const media = window.matchMedia("(max-width:799px)");
    const update = () => setIsMobile(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!wideDiff && !(selection && isMobile)) return;
    const panel = document.querySelector(
      wideDiff ? ".diff-modal" : ".inspector",
    ) as HTMLElement | null;
    if (!panel) return;
    const prior = document.activeElement as HTMLElement | null;
    const targets = () =>
      Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]',
        ),
      ).filter((element) => element.offsetParent !== null);
    const frame = requestAnimationFrame(() => {
      if (!panel?.contains(document.activeElement)) targets()[0]?.focus();
    });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (wideDiff) setWideDiff(false);
        else setSelection(null);
      }
      if (event.key === "Tab") {
        const focusable = targets();
        const first = focusable[0],
          last = focusable.at(-1);
        if (
          event.shiftKey &&
          (document.activeElement === first ||
            !panel.contains(document.activeElement))
        ) {
          event.preventDefault();
          last?.focus();
        } else if (
          !event.shiftKey &&
          (document.activeElement === last ||
            !panel.contains(document.activeElement))
        ) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKey);
      prior?.focus();
    };
  }, [wideDiff, selection?.id, isMobile]);
  useEffect(() => {
    if (!isMobile || !navOpen) return;
    const panel = document.querySelector<HTMLElement>(".workspace-sidebar");
    const prior = document.activeElement as HTMLElement | null;
    const targets = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          'button:not([disabled]),a[href],[tabindex="0"]',
        ) || [],
      ).filter((e) => e.offsetParent !== null);
    const frame = requestAnimationFrame(() => {
      if (!panel?.contains(document.activeElement)) targets()[0]?.focus();
    });
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setNavOpen(false);
      }
      if (e.key === "Tab") {
        const list = targets(),
          first = list[0],
          last = list.at(-1);
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            !panel?.contains(document.activeElement))
        ) {
          e.preventDefault();
          last?.focus();
        } else if (
          !e.shiftKey &&
          (document.activeElement === last ||
            !panel?.contains(document.activeElement))
        ) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", handle);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handle);
      prior?.focus();
    };
  }, [isMobile, navOpen]);
  const api = async (path: string, body?: unknown, signal?: AbortSignal) => {
    const response = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: {
        Authorization: `Bearer ${tokenRef.current}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
      signal,
    });
    const payload = await response.json();
    if (!response.ok) {
      const error: any = new Error(
        payload.error || `요청 실패 (${response.status})`,
      );
      error.status = response.status;
      error.code = typeof payload.code === "string" ? payload.code : undefined;
      throw error;
    }
    return payload;
  };
  const refresh = async (force = false) => {
    if (modeRef.current !== "local" || disposed.current) return;
    const ticket = snapshotRequest.current.begin(force);
    if (!ticket) return;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      ticket.controller.abort();
    }, 15000);
    const current = () =>
      !disposed.current &&
      modeRef.current === "local" &&
      snapshotRequest.current.isCurrent(ticket);
    try {
      const result = await api(
        "/api/snapshot",
        undefined,
        ticket.controller.signal,
      );
      if (!current()) return;
      const next = normalise(result.snapshot || result);
      snapshotRef.current = next;
      setSnapshot(next);
      setConnected(true);
      setError("");
      setNotice((value) => (value === RECORDS_BUSY_NOTICE ? "" : value));
    } catch (e: any) {
      if (disposed.current || modeRef.current !== "local") return;
      // A forced refresh/import/unmount owns the next state; old failures cannot overwrite it.
      if (!current() && !timedOut) return;
      if (!snapshotRequest.current.owns(ticket)) return;
      if (timedOut || current()) {
        if (!timedOut && isSnapshotBusy(e, snapshotRef.current !== null)) {
          // Keep the existing connection and receipt time; the normal next poll retries.
          setNotice(RECORDS_BUSY_NOTICE);
          setError("");
          return;
        }
        setNotice((value) => (value === RECORDS_BUSY_NOTICE ? "" : value));
        setConnected(false);
        setError(
          timedOut
            ? "프로젝트 응답 시간이 초과되었습니다."
            : e.message || "로컬 프로젝트에 연결할 수 없습니다.",
        );
      }
    } finally {
      clearTimeout(timeout);
      snapshotRequest.current.finish(ticket);
    }
  };
  useEffect(() => {
    disposed.current = false;
    const local =
      new URLSearchParams(window.location.search).get("mode") === "local";
    if (local) {
      modeRef.current = "local";
      setMode("local");
      const supplied = new URLSearchParams(
        window.location.hash.replace(/^#/, ""),
      ).get("token");
      if (supplied) {
        tokenRef.current = supplied;
        sessionStorage.setItem("ieum.workspace.token", supplied);
        history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
      } else
        tokenRef.current = sessionStorage.getItem("ieum.workspace.token") || "";
      setConnectionMode("로컬 프로젝트");
      if (!tokenRef.current) {
        setError(
          "연결 토큰이 없습니다. 에이전트에게 이 프로젝트의 관제 화면을 다시 열어 달라고 요청해 주세요.",
        );
        return;
      }
      refresh();
      const interval = setInterval(() => {
        refresh();
      }, 3000);
      return () => {
        disposed.current = true;
        clearInterval(interval);
        snapshotRequest.current.cancel();
        diffRequest.current.cancel();
      };
    }
    setSnapshot(makeDemo());
    setConnected(true);
    setConnectionMode("시뮬레이션");
    return () => {
      disposed.current = true;
      snapshotRequest.current.cancel();
      diffRequest.current.cancel();
    };
  }, []);
  const scopeTasks = useMemo(
    () =>
      snapshot
        ? workScope === "history"
          ? historyTasks(snapshot)
          : currentTasks(snapshot)
        : [],
    [snapshot, workScope],
  );
  const tasks = useMemo(
    () =>
      scopeTasks.filter(
        (t) =>
          (phaseFilter === "all" || phaseOf(t) === phaseFilter) &&
          (roleFilter === "all" || t.role === roleFilter) &&
          (statusFilter === "all" ||
            (statusFilter === "waiting" && waiting(t, snapshot!)) ||
            (statusFilter === "active" && active(t)) ||
            (statusFilter === "blocked" &&
              ["blocked", "failed", "stale"].includes(t.status)) ||
            (statusFilter === "done" && completed(t)) ||
            (statusFilter === "excluded" && excluded(t)) ||
            (statusFilter === "failed" && hasIssue(t, snapshot!))) &&
          (!query ||
            `${t.id} ${t.title} ${t.role} ${ROLE[t.role] || ""}`
              .toLowerCase()
              .includes(query.toLowerCase())),
      ) || [],
    [snapshot, scopeTasks, query, statusFilter, phaseFilter, roleFilter],
  );
  const relatedTasks = useMemo(() => {
    if (!snapshot || !selection) return [];
    if (selection.kind === "task")
      return snapshot.tasks.filter((t) => t.id === selection.id);
    if (selection.kind === "artefact")
      return snapshot.tasks.filter((t) =>
        t.artefact_ids.includes(selection.id),
      );
    if (selection.kind === "requirement")
      return snapshot.tasks.filter((t) =>
        t.requirement_ids.includes(selection.id),
      );
    return snapshot.tasks.filter(
      (t) =>
        t.agent_id === selection.id ||
        t.owner === selection.id ||
        (
          snapshot.agents.find((a) => a.id === selection.id)?.task_ids || []
        ).includes(t.id) ||
        snapshot.agents.find((a) => a.id === selection.id)?.task_id === t.id,
    );
  }, [snapshot, selection]);
  const task = relatedTasks[0];
  const selectedArtefact =
    selection?.kind === "artefact"
      ? snapshot?.artefacts.find((a) => a.id === selection.id)
      : null;
  const selectedAgent =
    selection?.kind === "agent"
      ? snapshot?.agents.find((a) => a.id === selection.id)
      : null;
  const taskGates = snapshot?.gates.filter((g) => g.task_id === task?.id) || [];
  const gate =
    [...taskGates]
      .reverse()
      .find((g) =>
        [
          "pending",
          "awaiting_approval",
          "awaiting_decision",
          "stale",
          "approved",
        ].includes(g.status),
      ) || taskGates.at(-1);
  const artefacts = selectedArtefact
    ? [selectedArtefact]
    : snapshot?.artefacts.filter(
        (a) =>
          task?.artefact_ids.includes(a.id) ||
          gate?.artefact_ids?.includes(a.id),
      ) || [];
  const artifactKey = artefacts.map((a) => a.path).join("|");
  const selectedFileHash = snapshot?.artefacts.find(
    (a) => a.path === filePath,
  )?.sha256;
  const gateHashKey = JSON.stringify({
    target_hashes: gate?.target_hashes || {},
    context_hash: gate?.context_hash,
  });
  useEffect(() => {
    setFilePath(
      selectedArtefact?.path ||
        Object.keys(gate?.target_hashes || {})[0] ||
        artefacts[0]?.path ||
        "",
    );
    setDiff(null);
    setDiffError("");
  }, [selection?.id, artifactKey, gate?.id]);
  useEffect(() => {
    diffRequest.current.cancel();
    setDiff(null);
    setDiffError("");
    if (!filePath || !snapshot) {
      setDiffBusy(false);
      return;
    }
    const ticket = diffRequest.current.begin(true)!;
    setDiffBusy(true);
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      ticket.controller.abort();
    }, 15000);
    const valid = () =>
      !disposed.current && diffRequest.current.isCurrent(ticket);
    const load =
      mode === "local"
        ? api(
            `/api/diff?path=${encodeURIComponent(filePath)}`,
            undefined,
            ticket.controller.signal,
          )
        : Promise.resolve(
            mode === "demo"
              ? demoDiff(filePath)
              : {
                  path: filePath,
                  baseline_available: false,
                  binary: false,
                  content: "스냅샷에는 파일 내용이 포함되지 않았습니다.",
                  diff: "",
                  sha256: selectedFileHash,
                },
          );
    load
      .then((result) => {
        if (!valid()) return;
        const latestHash = snapshotRef.current?.artefacts.find(
          (a) => a.path === filePath,
        )?.sha256;
        if (
          mode === "local" &&
          latestHash != null &&
          result.sha256 !== latestHash
        ) {
          setDiffError(
            "파일이 변경되었습니다. 새 버전을 확인한 뒤 다시 열어 주세요.",
          );
          return;
        }
        setDiff(result);
      })
      .catch((e) => {
        if (disposed.current || !diffRequest.current.owns(ticket)) return;
        if (timedOut || valid())
          setDiffError(
            timedOut ? "파일 응답 시간이 초과되었습니다." : e.message,
          );
      })
      .finally(() => {
        clearTimeout(timeout);
        if (diffRequest.current.owns(ticket)) {
          setDiffBusy(false);
          diffRequest.current.finish(ticket);
        }
      });
    return () => {
      clearTimeout(timeout);
      diffRequest.current.cancel();
    };
  }, [filePath, selectedFileHash, gateHashKey, mode, diffRetry]);
  const select = (s: Selection) => {
    const current = document.activeElement as HTMLElement | null;
    if (!current?.closest(".inspector")) selectionOpener.current = current;
    setSelection(s);
    setInspectTab(s.kind === "artefact" ? "diff" : "activity");
    setNotice("");
    setFocusTick((v) => v + 1);
  };
  const switchTab = (next: string) => {
    setTab(next);
    setSelection(null);
    setNavOpen(false);
    setMobileGraph(false);
  };
  const importSnapshot = async (event: React.ChangeEvent<HTMLInputElement>) => {
    try {
      const file = event.target.files?.[0];
      if (!file) return;
      if (file.size > 10 * 1024 * 1024)
        throw new Error("10MB 이하의 스냅샷을 선택해 주세요.");
      const raw = JSON.parse(await file.text());
      const imported = normalise({
        ...raw,
        mode: "snapshot",
        capabilities: {},
      });
      snapshotRequest.current.cancel();
      diffRequest.current.cancel();
      snapshotRef.current = imported;
      modeRef.current = "snapshot";
      setMode("snapshot");
      setSnapshot(imported);
      setSelection(null);
      setConnectionMode("읽기 전용 스냅샷");
      setConnected(true);
      setError("");
      setNotice(
        "파일을 이 브라우저에서만 읽었습니다. 원본 프로젝트와 연결되거나 업로드되지 않습니다.",
      );
    } catch (e: any) {
      setNotice(e.message);
    }
    event.target.value = "";
  };
  const reset = () => {
    setSnapshot(makeDemo());
    setSelection(null);
    setNotice("시뮬레이션을 처음 상태로 되돌렸습니다.");
    setPhaseFilter("all");
    setStatusFilter("all");
    setRoleFilter("all");
    setQuery("");
  };
  const resizeInspector = (event: React.PointerEvent) => {
    const start = event.clientX,
      initial = inspectorWidth;
    event.currentTarget.setPointerCapture(event.pointerId);
    const onMove = (e: PointerEvent) =>
      setInspectorWidth(
        Math.min(520, Math.max(360, initial + start - e.clientX)),
      );
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };
  const counts = taskSummary(
    snapshot ? currentTasks(snapshot) : [],
    snapshot || undefined,
  );
  const workers = snapshot ? nativeWorkers(snapshot) : [];
  const activeWorkers = connected
    ? workers.filter((a) => freshAgent(a, now)).length
    : 0;
  const count = counts.total;
  const done = counts.done;
  const running = snapshot
    ? snapshot.tasks.filter(
        (task) => connected && taskIsLive(snapshot, task, now, mode),
      ).length
    : 0;
  const runners = (snapshot?.runners || []) as any[];
  const currentRunner =
    runners.find((r) => connected && r.connected) || runners.at(-1);
  const supervisor = snapshot?.host?.supervisor;
  const providerIsLive = (provider: string) =>
    mode === "local" &&
    connected &&
    (runners.some((r) => r.provider === provider && r.connected) ||
      (snapshot?.agents || []).some(
        (a) => a.provider === provider && freshAgent(a, now),
      ));

  const inaccessibleFiles =
    snapshot?.artefacts.filter((a) => a.access_status === "unavailable")
      .length || 0;
  const nativeMonitor = snapshot?.host?.native_monitor;
  const nativeMonitorUnavailable =
    mode === "local" && nativeMonitor?.status === "unavailable";
  const supervisorNotice =
    mode === "local" &&
    connected &&
    supervisor?.configured &&
    ["retrying", "failed", "stopped"].includes(supervisor.status)
      ? supervisor.status === "retrying"
        ? `자동 실행기 연결을 다시 확인하고 있습니다${supervisor.next_retry_seconds != null ? ` (${supervisor.next_retry_seconds}초 후 재시도)` : ""}. ${snapshot?.host?.runner_connected ? "기존 실행기의 연결은 유지됩니다." : "프로젝트 데이터 수신과 자동 실행 연결은 별개입니다."}`
        : snapshot?.host?.runner_connected
          ? `기존 자동 실행기 연결은 유지됩니다. 이 관제의 추가 실행기 연결은 ${supervisor.status === "failed" ? "시작되지 않았습니다" : "중지되었습니다"}. ${supervisor.last_error || ""}`
          : `자동 실행기가 ${supervisor.status === "failed" ? "시작되지 않았습니다" : "중지되었습니다"}. ${supervisor.last_error || "현재 AI 대화에서 실행기 연결 상태를 확인해 주세요."}`
      : "";
  const runnerProvider =
    currentRunner?.provider === "claude"
      ? "Claude Code"
      : currentRunner?.provider === "codex"
        ? "Codex"
        : "호스트 미보고";
  const runnerStates: Record<string, string> = {
    starting: "연결 중",
    waiting: "자동 실행 대기",
    running: "자동 실행 중",
    stopping: "중지 중",
    stopped: "자동 실행 중지",
    failed: "실행기 확인 필요",
    disconnected: "실행기 연결 끊김",
  };
  const runnerText =
    mode === "local" && !connected
      ? "연결 상태 확인 필요"
      : supervisor?.configured &&
          supervisor.status === "failed" &&
          !snapshot?.host?.runner_connected
        ? "자동 실행기 시작 실패"
        : currentRunner
          ? `${runnerProvider} · ${runnerStates[currentRunner.status] || currentRunner.status}`
          : "자동 실행기 미연결";
  const recordedProgress = counts.verification_unavailable > 0;
  const progress = recordedProgress ? counts.recorded_percent : counts.percent;
  const cliMode =
    snapshot?.host?.execution_mode === "cli" ||
    snapshot?.host?.execution_mode === "mixed" ||
    (!snapshot?.host?.execution_mode &&
      Boolean(supervisor?.configured || currentRunner));
  const executionMode =
    mode === "demo"
      ? "시뮬레이션"
      : mode === "snapshot"
        ? "저장된 실행 기록"
        : snapshot?.host?.execution_mode === "mixed"
          ? "호스트 보고 + 별도 CLI"
          : snapshot?.host?.execution_mode === "unconnected"
            ? "실행 연결 미보고"
            : cliMode
              ? "별도 CLI 실행기"
              : "현재 호스트의 활동 보고";
  const lastActivity = snapshot ? activityOf(snapshot).at(-1) : undefined;
  const usageNote = snapshot ? usageCoverage(snapshot) : "사용량 미집계";
  const observedTokens = snapshot?.usage.tokens ?? snapshot?.usage.known_tokens;
  const observedCost = snapshot?.usage.cost ?? snapshot?.usage.known_cost_usd;
  const title =
    selectedArtefact?.path ||
    selectedAgent?.name ||
    task?.title ||
    selection?.id ||
    "";
  const upstream =
    (task?.depends_on
      .map((id) => snapshot?.tasks.find((t) => t.id === id))
      .filter(Boolean) as Task[]) || [];
  const downstream =
    snapshot?.tasks.filter((t) => t.depends_on.includes(task?.id || "")) || [];
  const toolbar = (
    <div className={`workspace-toolbar ${filtersOpen ? "filters-open" : ""}`}>
      <label className="search-field">
        <Icon name="search" size={16} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={tab === "artifacts" ? "파일 검색" : "작업 검색"}
          aria-label="작업 검색"
        />
        {query && (
          <button onClick={() => setQuery("")} aria-label="검색어 지우기">
            <Icon name="close" size={14} />
          </button>
        )}
      </label>
      {tab !== "artifacts" && (
        <button
          className="icon-button filter-toggle"
          aria-label="필터 보기"
          aria-expanded={filtersOpen}
          aria-controls="workspace-filters"
          onClick={() => setFiltersOpen((v) => !v)}
        >
          <Icon name="filter" size={17} />
        </button>
      )}
      {filtersOpen && tab !== "artifacts" && (
        <div id="workspace-filters" className="filter-panel">
          <select
            aria-label="작업 상태 필터"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="all">모든 상태</option>
            <option value="active">진행 중</option>
            <option value="waiting">대기 중</option>
            <option value="blocked">조건 대기·재검토</option>
            <option value="done">완료</option>
          </select>
          <select
            aria-label="담당 역할 필터"
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value)}
          >
            <option value="all">모든 역할</option>
            {[...new Set(snapshot?.tasks.map((t) => t.role) || [])]
              .filter(Boolean)
              .map((role) => (
                <option key={role} value={role}>
                  {ROLE[role] || role}
                </option>
              ))}
          </select>
          <select
            aria-label="업무 단계 필터"
            value={phaseFilter}
            onChange={(e) => setPhaseFilter(e.target.value)}
          >
            <option value="all">모든 단계</option>
            {PHASES.map((phase) => (
              <option key={phase.id} value={phase.id}>
                {phase.title}
              </option>
            ))}
          </select>
          <button
            className="quiet-button"
            onClick={() => {
              setStatusFilter("all");
              setPhaseFilter("all");
              setRoleFilter("all");
              setQuery("");
            }}
          >
            필터 초기화
          </button>
        </div>
      )}
      {tab !== "activity" && tab !== "artifacts" && (
        <button
          className="quiet-button mobile-view-switch"
          onClick={() => setMobileGraph(!mobileGraph)}
        >
          <Icon
            name={
              mobileGraph
                ? "log"
                : tabs.find((t) => t.id === tab)?.icon || "workflow"
            }
            size={16}
          />
          {mobileGraph ? "목록 보기" : "캔버스 보기"}
        </button>
      )}
    </div>
  );
  return (
    <div
      className={`ieum-workspace theme-light ${navOpen ? "nav-open" : ""} ${selection ? "has-inspector" : ""} ${tab === "activity" ? "is-observer" : ""}`}
      style={
        { "--inspector-width": `${inspectorWidth}px` } as React.CSSProperties
      }
    >
      <a className="skip-link" href="#workspace-content">
        작업 화면으로 건너뛰기
      </a>
      {isMobile && navOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="탐색 메뉴 닫기"
          onClick={() => setNavOpen(false)}
        />
      )}
      <aside
        className="workspace-sidebar"
        id="workspace-navigation"
        aria-label="프로젝트 탐색"
        role={isMobile && navOpen ? "dialog" : undefined}
        aria-modal={isMobile && navOpen ? true : undefined}
        aria-hidden={isMobile && !navOpen ? true : undefined}
        inert={isMobile && !navOpen ? true : undefined}
      >
        <button
          className="sidebar-close icon-button"
          aria-label="프로젝트 메뉴 닫기"
          onClick={() => setNavOpen(false)}
        >
          <Icon name="close" size={20} />
        </button>
        <a
          className="workspace-brand"
          href="https://agics-hsjeong.github.io/project-lifecycle-agent/"
        >
          <span className="brand-plate">
            <img
              src={`${base}/brand/symbol.png`}
              width="28"
              height="20"
              alt=""
            />
          </span>
          <span>
            <strong>프로젝트이음</strong>
            <small>PROJECT IEUM</small>
          </span>
        </a>
        <div className="sidebar-project">
          <Icon name="layers" size={17} />
          <span>
            <small>현재 프로젝트</small>
            <strong>{snapshot?.project.name || "프로젝트 연결 중"}</strong>
          </span>
        </div>
        <nav className="workspace-tabs" aria-label="관제 화면">
          {tabs.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? "active" : ""}
              onClick={() => switchTab(t.id)}
              aria-current={tab === t.id ? "page" : undefined}
            >
              <Icon name={t.icon} size={18} />
              <span>{t.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          {isMobile && mode !== "local" && (
            <div className="sidebar-mobile-actions">
              {mode === "demo" && (
                <button
                  aria-pressed={demoPlaying}
                  onClick={() => setDemoPlaying((v) => !v)}
                >
                  <Icon name="clock" size={16} />
                  {demoPlaying ? "데모 일시 정지" : "데모 재생"}
                </button>
              )}
              <button
                onClick={() => {
                  setNavOpen(false);
                  importRef.current?.click();
                }}
              >
                <Icon name="file" size={16} />
                스냅샷 열기
              </button>
            </div>
          )}
          <div className="sidebar-hosts" aria-label="호스트 연결">
            {[
              ...new Set(
                [
                  ...(snapshot?.agents || []).map((a) => a.provider),
                  ...runners.map((r) => r.provider),
                ].filter((p) => p === "codex" || p === "claude"),
              ),
            ].map((provider) => (
              <div key={provider}>
                <Icon name="agent" size={15} />
                <strong>
                  {provider === "claude" ? "Claude Code" : "Codex"}
                </strong>
                <span
                  className={`host-dot ${providerIsLive(provider) ? "connected" : ""}`}
                />
                <small>
                  {mode === "demo"
                    ? "예시"
                    : mode === "snapshot"
                      ? "기록"
                      : providerIsLive(provider)
                        ? "연결됨"
                        : "보고 기록"}
                </small>
              </div>
            ))}
            {!snapshot?.agents.length && !runners.length && (
              <p>호스트 활동 대기</p>
            )}
          </div>
          <a
            className="sidebar-guide"
            href="https://agics-hsjeong.github.io/project-lifecycle-agent/docs/guides/workspace/"
            target="_blank"
            rel="noreferrer"
          >
            <Icon name="info" size={16} />
            사용 가이드
          </a>
          <small className="sidebar-version">v{version} · 베타</small>
        </div>
      </aside>
      <header className="workspace-header">
        <div className="breadcrumb">
          <button
            className="mobile-menu-toggle icon-button"
            aria-label="프로젝트 메뉴 열기"
            aria-expanded={navOpen}
            aria-controls="workspace-navigation"
            onClick={() => setNavOpen(true)}
          >
            <Icon name="log" />
          </button>
          <span>워크스페이스</span>
          <span className="breadcrumb-divider">/</span>
          <strong>{snapshot?.project.name || "프로젝트 연결 중"}</strong>
        </div>
        <div className="context-actions">
          <span
            className={`connection-state ${connected && mode === "local" ? "is-connected" : ""}`}
          >
            <i />
            {mode === "snapshot"
              ? "읽기 전용"
              : mode === "demo"
                ? "시뮬레이션"
                : connected
                  ? "데이터 연결됨"
                  : "연결 확인 필요"}
          </span>
          <span className="last-synced">
            {snapshot?.captured_at
              ? `${mode === "snapshot" ? "저장 시각" : "마지막 수신"} ${prettyTime(snapshot.captured_at)}`
              : "수신 시각 미보고"}
          </span>
          {mode === "local" ? (
            <button
              className="icon-button"
              onClick={() => refresh(true)}
              aria-label="프로젝트 새로고침"
            >
              <Icon name="refresh" size={16} />
            </button>
          ) : (
            <>
              <button
                onClick={() => importRef.current?.click()}
                className="quiet-button snapshot-open"
              >
                <Icon name="file" size={15} />
                스냅샷 열기
              </button>
              <input
                ref={importRef}
                type="file"
                accept=".json,application/json"
                onChange={importSnapshot}
                hidden
              />
              {mode === "demo" && (
                <>
                  <button
                    className="quiet-button demo-playback"
                    aria-pressed={demoPlaying}
                    onClick={() => setDemoPlaying((v) => !v)}
                  >
                    {demoPlaying ? "데모 일시 정지" : "데모 재생"}
                  </button>
                  <button
                    className="icon-button"
                    onClick={reset}
                    aria-label="시뮬레이션 초기화"
                  >
                    <Icon name="refresh" size={16} />
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </header>
      {(error ||
        supervisorNotice ||
        nativeMonitorUnavailable ||
        inaccessibleFiles > 0) && (
        <div
          className="workspace-notices"
          role="region"
          aria-label="실행·연결 알림"
          tabIndex={0}
        >
          {inaccessibleFiles > 0 && (
            <div
              className="native-monitor-notice artefact-access-notice"
              role="status"
              aria-live="polite"
            >
              <Icon name="info" size={17} />
              <div>
                <strong>
                  파일 접근 지연 · {inaccessibleFiles}개 버전 확인 불가
                </strong>
                <p>
                  해당 파일의 받기와 미리보기를 잠시 중단합니다. 다른 작업과
                  로그는 계속 갱신합니다.
                </p>
              </div>
            </div>
          )}
          {mode === "local" && nativeMonitor?.status === "waiting" && (
            <div
              className="native-monitor-waiting"
              role="status"
              aria-live="polite"
              data-native-monitor-state="waiting"
            >
              <Icon name="info" size={17} />
              <div>
                <strong>기록 동기화 대기</strong>
                <p>
                  프로젝트 기록 작업이 끝나면 자동으로 이어 받습니다. 마지막
                  수신 상태를 유지합니다.
                </p>
              </div>
            </div>
          )}
          {nativeMonitorUnavailable && (
            <div
              className="native-monitor-notice"
              role="status"
              aria-live="polite"
              data-native-monitor-state="unavailable"
            >
              <Icon name="info" size={17} />
              <div>
                <strong>실행 관측 지연 · 보관 이벤트 재수신 중</strong>
                <p>
                  {nativeMonitor?.last_error ||
                    "현재 작업 상태가 늦게 표시될 수 있습니다. 작업 목록과 마지막 수신 기록을 유지합니다."}
                </p>
              </div>
            </div>
          )}
          {error && (
            <div role="alert" className="connection-error">
              <Icon name="info" size={16} />
              <span>{error} 마지막 수신 상태를 유지합니다.</span>
            </div>
          )}
          {supervisorNotice && (
            <div
              role="status"
              aria-live="polite"
              className="connection-error"
              data-supervisor-state={supervisor.status}
            >
              <Icon name="info" size={16} />
              <span>{supervisorNotice}</span>
            </div>
          )}
        </div>
      )}
      <div className="workspace-overview">
        <div className="overview-heading">
          <div>
            <h1>
              {tab === "activity"
                ? "실행 현황"
                : tab === "workflow"
                  ? "워크플로우"
                  : tab === "map"
                    ? "작업 지도"
                    : tab === "artifacts"
                      ? "산출물"
                      : "타임라인"}
            </h1>
            <p>
              {mode === "demo"
                ? "가상 프로젝트 · 역할별 작업과 실제 연결 방식 미리보기"
                : mode === "snapshot"
                  ? "저장된 작업·활동 기록 · 원본 프로젝트와 연결되지 않음"
                  : snapshot?.project.name ||
                    "실제 프로젝트의 작업과 활동을 연결합니다."}
            </p>
          </div>
          <div
            className="overall-progress"
            data-progress-basis={recordedProgress ? "recorded" : "current"}
            aria-label={
              recordedProgress
                ? `실행 기록 ${counts.recorded_done}개 완료 · 현재 근거 확인 불가 ${counts.verification_unavailable}건 · 현재 검증 확인 ${done}개`
                : `현재 범위 적용 대상 ${counts.applicable}개 중 ${done}개 완료 · 적용 제외 ${counts.excluded}개`
            }
          >
            <div>
              <strong>
                {recordedProgress
                  ? `실행 기록 ${counts.recorded_done} / ${counts.recorded_applicable} 완료`
                  : counts.applicable
                    ? `현재 범위 ${done} / ${counts.applicable} 완료`
                    : "적용 대상 없음"}
              </strong>
              <b>
                {progress == null ? "—" : progress}
                {progress != null && <small>%</small>}
              </b>
            </div>
            {progress != null && (
              <div
                role="progressbar"
                aria-label={
                  recordedProgress
                    ? "실행 기록 기준 작업 완료 비율 · 현재 검증과 별개"
                    : "현재 범위 적용 대상 작업 완료 비율"
                }
                aria-valuenow={progress}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <i
                  style={{
                    width: `${progress}%`,
                    background: recordedProgress ? "#647c89" : undefined,
                  }}
                />
              </div>
            )}
            {recordedProgress && (
              <small className="progress-verification-note">
                현재 근거 확인 불가 {counts.verification_unavailable}건 · 현재
                검증 확인 {done} / {counts.applicable}
              </small>
            )}
            <small>
              <span>{counts.active} 진행 중</span>
              <span>·</span>
              <span>{counts.waiting} 대기</span>
              <span>·</span>
              <span className={counts.attention ? "has-attention" : ""}>
                {counts.attention} 확인 필요
              </span>
              <span>·</span>
              <span>{counts.excluded} 적용 제외</span>
              {snapshot &&
                currentTasks(snapshot).some(
                  (t) => active(t) && !taskIsLive(snapshot, t, now, mode),
                ) && <span>· 활동 확인 필요</span>}
            </small>
          </div>
        </div>
        <div className="execution-hud" aria-label="데이터와 실제 실행 상태">
          <div>
            <span>실행 방식</span>
            <strong>{executionMode}</strong>
            <small>
              {mode === "local"
                ? cliMode
                  ? runnerText
                  : "이 관제에 전달된 호스트 활동만 표시"
                : "실제 실행기에 연결되지 않음"}
            </small>
          </div>
          <div>
            <span>실제 작업 · 활성 작업자 {activeWorkers}명</span>
            <strong>
              {mode === "snapshot"
                ? "스냅샷 시점의 기록"
                : mode === "demo"
                  ? "합성 시나리오"
                  : running
                    ? `${running}개 작업에서 실제 활동 보고`
                    : activeWorkers
                      ? `작업자 ${activeWorkers}명 실제 활동 관측`
                      : counts.active
                        ? "진행 기록의 활동 확인 필요"
                        : counts.attention
                          ? `${counts.attention}개 작업 확인 필요`
                          : "보고된 실행 작업 없음"}
            </strong>
            <small>
              {lastActivity?.at || snapshot?.host?.latest_activity_at
                ? `마지막 행동 ${prettyTime(lastActivity?.at || snapshot?.host?.latest_activity_at)}`
                : "행동 시각 미보고"}
            </small>
          </div>
          <div>
            <span>관측 리소스</span>
            <strong>
              {observedTokens != null
                ? `${Number(observedTokens).toLocaleString()} tokens`
                : "토큰 미집계"}
              {observedCost != null ? ` · $${observedCost}` : ""}
            </strong>
            <small>{usageNote}</small>
          </div>
          <p>
            {progress == null
              ? "적용 대상 작업 없음"
              : recordedProgress
                ? "실행 완료 기록 기준 비율 · 파일 접근 복구 후 현재 검증 근거를 다시 확인합니다."
                : "작업 개수 기준 완료율 · 공수·일정·품질 진척률이 아닙니다."}
          </p>
        </div>
      </div>
      {snapshot?.current_scope && (
        <div className="scope-bar" aria-label="작업 집계 범위">
          <div>
            <small>{snapshot.current_scope.label || "현재 작업 범위"}</small>
            <strong>{snapshot.current_scope.title}</strong>
            <span>
              현재 {currentTasks(snapshot).length}개 · 전체 원장{" "}
              {snapshot.tasks.length}개
            </span>
          </div>
          <div
            className="scope-switch"
            role="group"
            aria-label="현재 작업과 실행 이력"
          >
            <button
              aria-pressed={workScope === "current"}
              onClick={() => {
                setWorkScope("current");
                setSelection(null);
              }}
            >
              현재 작업
            </button>
            <button
              aria-pressed={workScope === "history"}
              onClick={() => {
                setWorkScope("history");
                setSelection(null);
              }}
            >
              실행 이력 <b>{historyTasks(snapshot).length}</b>
            </button>
          </div>
        </div>
      )}
      {workScope === "history" && snapshot && (
        <div className="scope-history-note" role="status">
          과거 작업과 실패·재검증 기록입니다. 위 완료율은 현재 범위만
          집계합니다. 실행 기록 {(snapshot.history_runs || []).length}건을
          보존하고 있습니다.
        </div>
      )}
      {(tab === "artifacts" ||
        (tab !== "activity" &&
          (!tasks.length || (isMobile && !mobileGraph)))) &&
        toolbar}
      <main id="workspace-content" className="workspace-main">
        <section
          className={`workspace-view ${mobileGraph ? "mobile-canvas" : "mobile-list"}`}
          aria-label={tabs.find((t) => t.id === tab)?.label}
        >
          {!snapshot ? (
            <Empty
              text={
                error
                  ? "로컬 연결을 확인해 주세요"
                  : "프로젝트를 불러오고 있습니다"
              }
              detail={
                error
                  ? "연결 실패 시 시뮬레이션으로 전환하지 않습니다."
                  : "역할별 작업과 실제 실행 기록을 확인합니다."
              }
            />
          ) : tab === "artifacts" ? (
            <div className="artifact-library">
              <header>
                <h2>연결된 파일</h2>
                <span>
                  {
                    snapshot.artefacts.filter(
                      (a) =>
                        !query ||
                        a.path.toLowerCase().includes(query.toLowerCase()),
                    ).length
                  }
                  개 산출물
                </span>
              </header>
              <div className="artifact-library-list">
                {snapshot.artefacts
                  .filter(
                    (a) =>
                      !query ||
                      a.path.toLowerCase().includes(query.toLowerCase()),
                  )
                  .map((a) => (
                    <button
                      key={a.id}
                      onClick={() => select({ kind: "artefact", id: a.id })}
                    >
                      <Icon name="file" size={19} />
                      <span>
                        <strong>{a.path.split("/").at(-1)}</strong>
                        <small className="mono">{a.path}</small>
                        <small className="artifact-kind">
                          {artefactKind(a.path)} ·{" "}
                          {a.access_status === "unavailable"
                            ? "접근 불가 · 버전 미확인"
                            : a.exists === false
                              ? "파일 없음"
                              : a.sha256
                                ? "현재 버전 등록"
                                : "버전 미확인"}
                        </small>
                      </span>
                      <span className="artifact-hash mono">
                        {a.sha256 ? a.sha256.slice(0, 10) : "해시 미보고"}
                      </span>
                      <Icon name="chevron" size={15} />
                    </button>
                  ))}
              </div>
              {!snapshot.artefacts.some(
                (a) =>
                  !query || a.path.toLowerCase().includes(query.toLowerCase()),
              ) && (
                <Empty
                  text="표시할 파일이 없습니다"
                  detail="검색 조건 또는 실제 산출물 연결을 확인하세요."
                />
              )}
            </div>
          ) : tab === "activity" ? (
            <>
              {workScope === "history" && (
                <RunHistory runs={snapshot.history_runs || []} />
              )}
              <Observer
                snapshot={snapshot}
                tasks={tasks}
                selection={selection}
                select={select}
                now={now}
                mode={mode}
                connected={connected}
                toolbar={toolbar}
              />
            </>
          ) : !tasks.length ? (
            <Empty
              text={
                snapshot.tasks.length
                  ? "표시할 작업이 없습니다"
                  : "등록된 작업이 없습니다"
              }
              detail={
                snapshot.tasks.length
                  ? "검색과 필터를 변경해 주세요."
                  : "에이전트에게 현재 프로젝트의 작업과 실행 계획을 등록해 달라고 요청하세요."
              }
            />
          ) : (
            <>
              <div className="desktop-canvas">
                {tab === "workflow" ? (
                  <Workflow
                    snapshot={snapshot}
                    tasks={tasks}
                    selection={selection}
                    onSelect={select}
                    focusTick={focusTick}
                    toolbar={toolbar}
                  />
                ) : tab === "map" ? (
                  <KnowledgeMap
                    snapshot={snapshot}
                    tasks={tasks}
                    selection={selection}
                    onSelect={select}
                    toolbar={toolbar}
                  />
                ) : (
                  <Wbs
                    snapshot={snapshot}
                    tasks={tasks}
                    selection={selection}
                    onSelect={select}
                    toolbar={toolbar}
                  />
                )}
              </div>
              <div className="mobile-task-list">
                <TaskList
                  tasks={tasks}
                  selection={selection}
                  onSelect={select}
                />
              </div>
            </>
          )}
        </section>
        {selection && snapshot && (
          <>
            <button
              className="inspector-backdrop"
              onClick={() => setSelection(null)}
              aria-label="상세 패널 닫기"
            />
            <aside
              onKeyDown={(event) => {
                if (event.key === "Escape" && !isMobile && !wideDiff) {
                  event.preventDefault();
                  setSelection(null);
                  requestAnimationFrame(() => selectionOpener.current?.focus());
                }
              }}
              className="inspector"
              aria-label="작업 상세"
              role={isMobile ? "dialog" : undefined}
              aria-modal={isMobile ? true : undefined}
            >
              <div
                className="inspector-resize"
                onPointerDown={resizeInspector}
                role="separator"
                aria-label="상세 패널 너비 조절"
                aria-orientation="vertical"
                aria-valuemin={360}
                aria-valuemax={520}
                aria-valuenow={inspectorWidth}
                aria-valuetext={`${inspectorWidth}픽셀`}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "ArrowLeft")
                    setInspectorWidth((v) => Math.min(520, v + 16));
                  if (e.key === "ArrowRight")
                    setInspectorWidth((v) => Math.max(360, v - 16));
                }}
              />
              <div className="inspector-heading">
                <span>
                  <Icon
                    name={
                      selection.kind === "task"
                        ? "workflow"
                        : selection.kind === "agent"
                          ? "agent"
                          : "file"
                    }
                    size={15}
                  />
                  TASK OBSERVER
                </span>
                <button
                  onClick={() => setSelection(null)}
                  className="icon-button"
                  aria-label="작업 상세 닫기"
                >
                  <Icon name="close" />
                </button>
              </div>
              <div className="inspector-title">
                {task?.stage_unassigned && (
                  <p className="stage-unassigned">
                    업무 단계 연결 필요 · 현재 프로젝트 단계에 임시 표시합니다.
                  </p>
                )}
                <span className="mono">{selection.id}</span>
                <h2>{title}</h2>
                <div>
                  {task && (
                    <span>{ROLE[task.role] || task.role || "담당 미정"}</span>
                  )}
                  {task && (
                    <>
                      <StatusBadge status={task.status} task={task} />
                    </>
                  )}
                </div>
              </div>
              <div
                className="inspector-tabs"
                role="tablist"
                aria-label="상세 정보 종류"
              >
                {inspectTabs.map(([id, label]) => (
                  <button
                    key={id}
                    id={`inspector-tab-${id}`}
                    role="tab"
                    tabIndex={inspectTab === id ? 0 : -1}
                    aria-selected={inspectTab === id}
                    aria-controls="inspector-content"
                    onClick={() => setInspectTab(id)}
                    onKeyDown={(event) => {
                      if (
                        !["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                          event.key,
                        )
                      )
                        return;
                      event.preventDefault();
                      const current = inspectTabs.findIndex(
                        ([key]) => key === id,
                      );
                      const next =
                        event.key === "Home"
                          ? 0
                          : event.key === "End"
                            ? inspectTabs.length - 1
                            : (current +
                                (event.key === "ArrowRight" ? 1 : -1) +
                                inspectTabs.length) %
                              inspectTabs.length;
                      setInspectTab(inspectTabs[next][0]);
                      document
                        .getElementById(`inspector-tab-${inspectTabs[next][0]}`)
                        ?.focus();
                    }}
                    className={inspectTab === id ? "active" : ""}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div
                className="inspector-content"
                id="inspector-content"
                role="tabpanel"
                aria-labelledby={`inspector-tab-${inspectTab}`}
                tabIndex={0}
              >
                {relatedTasks.length > 1 && (
                  <div className="info-box">
                    연결된 작업 {relatedTasks.length}개
                    <div className="related-buttons">
                      {relatedTasks.map((t) => (
                        <button
                          key={t.id}
                          onClick={() => select({ kind: "task", id: t.id })}
                        >
                          {t.title}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {inspectTab === "activity" && (
                  <>
                    <div className="section-label">
                      실제로 보고된 행동{" "}
                      <span>
                        {
                          activityOf(snapshot).filter(
                            (e) => !task || e.task_id === task.id,
                          ).length
                        }
                      </span>
                    </div>
                    {task && <VerificationBadge task={task} />}
                    {task && (
                      <div className="info-box">
                        <p>
                          {task.latest_action ||
                            task.latest_activity?.summary ||
                            "아직 세부 행동이 보고되지 않았습니다."}
                        </p>
                      </div>
                    )}
                    <ActivityFeed
                      events={activityOf(snapshot).filter(
                        (e) => !task || e.task_id === task.id,
                      )}
                      now={now}
                      select={select}
                    />
                    {task?.waiting_reason && (
                      <p className="fine-print">
                        대기 이유: {task.waiting_reason}
                      </p>
                    )}
                    {task?.blocked_reason && (
                      <p className="fine-print">
                        현재 조건: {task.blocked_reason}
                      </p>
                    )}
                  </>
                )}
                {inspectTab === "diff" && (
                  <>
                    {artefacts.length ? (
                      <>
                        <div className="section-label">
                          검토 대상 산출물 <span>{artefacts.length}</span>
                        </div>
                        <ArtefactDelivery
                          file={artefacts.find((a) => a.path === filePath)}
                          mode={mode}
                          token={tokenRef.current}
                        />
                        <select
                          className="artifact-select"
                          value={filePath}
                          onChange={(e) => setFilePath(e.target.value)}
                          aria-label="비교할 산출물"
                        >
                          {artefacts.map((a) => (
                            <option key={a.id} value={a.path}>
                              {a.path}
                            </option>
                          ))}
                        </select>
                        {diffBusy ? (
                          <p className="muted">
                            파일 버전을 확인하고 있습니다.
                          </p>
                        ) : diffError ? (
                          <div role="alert" className="info-box danger">
                            <div>
                              <p>{diffError}</p>
                              <button
                                className="quiet-button"
                                onClick={() =>
                                  setDiffRetry((value) => value + 1)
                                }
                              >
                                산출물 다시 불러오기
                              </button>
                            </div>
                          </div>
                        ) : (
                          diff && (
                            <>
                              <div className="diff-meta">
                                <span>
                                  {diff.binary
                                    ? "바이너리 파일"
                                    : diff.baseline_available
                                      ? "이전 기준과 비교"
                                      : "비교 기준 없음 · 현재 내용"}
                                </span>
                                <button
                                  className="icon-button"
                                  onClick={() => setWideDiff(true)}
                                  aria-label="Diff 넓게 보기"
                                >
                                  <Icon name="expand" size={15} />
                                </button>
                              </div>
                              <DiffContent diff={diff} />
                              <div className="file-hash">
                                <Icon name="shield" size={13} />
                                <span>
                                  SHA256{" "}
                                  {diff.sha256?.slice(0, 16) || "확인 불가"}
                                </span>
                              </div>
                            </>
                          )
                        )}
                      </>
                    ) : (
                      <Empty
                        text="연결된 산출물이 없습니다"
                        detail="원장이나 실행 계획에 실제 파일 경로가 연결되면 표시됩니다."
                      />
                    )}
                  </>
                )}
                {inspectTab === "links" && (
                  <>
                    <section className="inspector-section">
                      <h3>
                        선행 작업 <span>{upstream.length}</span>
                      </h3>
                      {upstream.length ? (
                        upstream.map((t) => (
                          <TaskLink key={t.id} task={t} select={select} />
                        ))
                      ) : (
                        <p className="muted">기록된 선행 작업이 없습니다.</p>
                      )}
                    </section>
                    <section className="inspector-section">
                      <h3>
                        영향받는 후속 작업 <span>{downstream.length}</span>
                      </h3>
                      {downstream.length ? (
                        downstream.map((t) => (
                          <TaskLink key={t.id} task={t} select={select} />
                        ))
                      ) : (
                        <p className="muted">기록된 후속 작업이 없습니다.</p>
                      )}
                    </section>
                    <section className="inspector-section">
                      <h3>요구사항</h3>
                      <div className="related-buttons">
                        {task?.requirement_ids.map((id) => (
                          <button
                            key={id}
                            onClick={() => select({ kind: "requirement", id })}
                          >
                            {id}
                          </button>
                        ))}
                      </div>
                    </section>
                    <section className="inspector-section">
                      <h3>산출물 · 근거</h3>
                      {artefacts.map((a) => (
                        <button
                          className="artifact-link"
                          key={a.id}
                          onClick={() => {
                            setFilePath(a.path);
                            setInspectTab("diff");
                          }}
                        >
                          <Icon name="file" size={16} />
                          <span>{a.path}</span>
                        </button>
                      ))}
                    </section>
                    <p className="fine-print">
                      원장과 실행 기록에 명시된 연결만 표시합니다. 같은
                      역할이라는 이유로 의존 관계를 만들지 않습니다.
                    </p>
                  </>
                )}
                {inspectTab === "schedule" && (
                  <>
                    <div className="resource-grid">
                      <Resource
                        label="예정 시작"
                        value={task?.start || "미지정"}
                      />
                      <Resource
                        label="예정 종료"
                        value={task?.end || task?.due_date || "미지정"}
                      />
                      <Resource
                        label="실제 시작"
                        value={task?.actual_start || "실행 기록 없음"}
                      />
                      <Resource
                        label="실제 완료"
                        value={task?.actual_end || "완료 기록 없음"}
                      />
                      <Resource
                        label="기록된 진척"
                        value={
                          task?.progress != null
                            ? `${task.progress}%`
                            : completed(task || ({ status: "todo" } as Task))
                              ? "100%"
                              : "미집계"
                        }
                      />
                      <Resource
                        label="담당 역할"
                        value={ROLE[task?.role || ""] || task?.role || "미지정"}
                      />
                    </div>
                    <section className="inspector-section">
                      <h3>실행 연결</h3>
                      <Resource
                        label="원장 상태"
                        value={
                          task?.ledger_status
                            ? labels[task.ledger_status] || task.ledger_status
                            : labels[task?.status || ""] || "미보고"
                        }
                      />
                      <Resource
                        label="실제 실행"
                        value={
                          task
                            ? executionLabel(task) ||
                              (
                                {
                                  starting: "실행 준비 중",
                                  preparing: "프로젝트 준비 중",
                                  running: "실행 중",
                                  completed: "실행 종료",
                                  failed: "실행 실패",
                                  idle: "호스트 대기 중",
                                  disconnected: "연결 확인 필요",
                                } as Record<string, string>
                              )[task.execution_state] ||
                              "실행 관측 없음"
                            : "실행 관측 없음"
                        }
                      />
                      <Resource
                        label="실제 작업자 ID"
                        value={
                          selectedAgent?.native_agent_id ||
                          task?.native_agent_id ||
                          "작업 연결 대기"
                        }
                      />
                      <Resource
                        label="세션 ID"
                        value={
                          selectedAgent?.session_id ||
                          task?.session_id ||
                          "미보고"
                        }
                      />
                      <Resource
                        label="관측 출처"
                        value={
                          task?.runtime_source === "codex_rollout"
                            ? "Codex 실행 이벤트"
                            : task?.runtime_source === "host_hook"
                              ? "호스트 수명주기 이벤트"
                              : "수동·호스트 보고"
                        }
                      />
                      <Resource
                        label="RUN / STEP"
                        value={
                          task?.run_id
                            ? `${task.run_id} / ${task.step_id || "—"}`
                            : "연결 기록 없음"
                        }
                      />
                      <Resource label="자동 실행기" value={runnerText} />
                      {currentRunner?.notes && (
                        <p className="fine-print">{currentRunner.notes}</p>
                      )}
                      <Resource
                        label="호스트"
                        value={
                          snapshot.host?.connected
                            ? "실행 호스트 연결됨"
                            : "실행 호스트 미연결"
                        }
                      />
                      <p className="fine-print">
                        실제 프로세스 시작과 행동 보고를 기준으로 표시합니다.
                        실행기가 연결되어 있어도 수행 중인 작업이 없을 수
                        있습니다.
                      </p>
                    </section>
                    <section className="inspector-section">
                      <h3>수집된 리소스</h3>
                      <p className="fine-print">{usageNote}</p>
                      <Resource
                        label="관측 토큰"
                        value={
                          observedTokens != null
                            ? Number(observedTokens).toLocaleString()
                            : "미집계"
                        }
                      />
                      <Resource
                        label="관측 API 비용"
                        value={
                          observedCost != null ? `$${observedCost}` : "미집계"
                        }
                      />
                    </section>
                  </>
                )}
                {inspectTab === "logs" && (
                  <>
                    <div className="log-heading">
                      <span
                        className={`connection-indicator ${connected ? "connected" : ""}`}
                      />
                      {mode === "local"
                        ? "3초마다 프로젝트 기록 갱신"
                        : mode === "demo"
                          ? "가상 시나리오 실행 기록"
                          : "스냅샷 시점의 기록"}
                    </div>
                    <div className="event-log">
                      {snapshot.logs.length ? (
                        snapshot.logs
                          .slice(-80)
                          .reverse()
                          .map((log, i) => (
                            <article key={log.id || i}>
                              <time>
                                {prettyTime(log.at || log.timestamp || "")}
                              </time>
                              <strong>
                                {log.actor ||
                                  log.type ||
                                  log.action ||
                                  "프로젝트 기록"}
                              </strong>
                              <p>
                                {log.message ||
                                  log.summary ||
                                  JSON.stringify(log.details || {})}
                              </p>
                            </article>
                          ))
                      ) : (
                        <p className="muted">아직 기록된 이벤트가 없습니다.</p>
                      )}
                    </div>
                  </>
                )}
                {inspectTab === "guidelines" && (
                  <>
                    <div className="guideline-heading">
                      <Icon name="agent" size={28} />
                      <h3>요청과 대화는 코딩 도구에서.</h3>
                      <p>
                        관제는 역할별 수행·검토·수정의 흐름을 관찰하는
                        화면입니다.
                      </p>
                    </div>
                    <ol className="guidelines">
                      <li>
                        Codex 또는 Claude Code에 원하는 결과와 질문을
                        전달하세요.
                      </li>
                      <li>
                        에이전트가 역할을 나누고 검토·수정을 이어가면 실제
                        행동이 기록됩니다.
                      </li>
                      <li>
                        사람의 판단이 필요한 사항은 에이전트 채팅에서
                        확인하세요.
                      </li>
                      <li>
                        전체 진척은 완료 작업 수 기준이며 보고되지 않은
                        진행률이나 비용은 추정하지 않습니다.
                      </li>
                    </ol>
                  </>
                )}
              </div>
              <div className="observer-inspector-note">
                <Icon name="info" size={16} />
                <span>
                  이 화면은 실행을 관찰합니다. 요청·피드백·판단은 사용 중인 코딩
                  에이전트와 대화하세요.
                </span>
              </div>
            </aside>
          </>
        )}
      </main>
      <footer className="workspace-footer">
        <span>
          <Icon name={mode === "local" ? "shield" : "info"} size={13} />
          {mode === "local"
            ? "실제 프로젝트 활동 · 요청과 대화는 Codex·Claude에서 이어갑니다."
            : mode === "demo"
              ? "시뮬레이션 · 실제 에이전트 실행과 비용이 아닙니다."
              : "읽기 전용 · 원본 파일을 변경하지 않습니다."}
        </span>
        <span className="mono">
          {snapshot?.revision
            ? `REV ${snapshot.revision.slice(0, 8)}`
            : "REV —"}
        </span>
      </footer>
      {notice && (
        <div
          className="workspace-notice"
          role="status"
          data-records-busy={notice === RECORDS_BUSY_NOTICE ? true : undefined}
        >
          <Icon name="info" size={18} />
          <span>{notice}</span>
          <button
            className="icon-button"
            onClick={() => setNotice("")}
            aria-label="알림 닫기"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      )}
      {wideDiff && diff && (
        <div className="diff-modal-backdrop" onClick={() => setWideDiff(false)}>
          <section
            className="diff-modal"
            role="dialog"
            aria-modal="true"
            aria-label="변경점 넓게 보기"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") setWideDiff(false);
            }}
          >
            <header>
              <span>{filePath}</span>
              <button
                autoFocus
                className="icon-button"
                onClick={() => setWideDiff(false)}
                aria-label="변경점 닫기"
              >
                <Icon name="close" />
              </button>
            </header>
            <DiffContent diff={diff} />
          </section>
        </div>
      )}
    </div>
  );
}
function Resource({ label, value }: { label: string; value: string }) {
  return (
    <div className="resource">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function TaskLink({
  task,
  select,
}: {
  task: Task;
  select: (s: Selection) => void;
}) {
  return (
    <button
      className="task-link"
      onClick={() => select({ kind: "task", id: task.id })}
    >
      <span>
        <small className="mono">{task.id}</small>
        <strong>{task.title}</strong>
      </span>
      <StatusBadge status={task.status} task={task} />
    </button>
  );
}
function DiffContent({ diff }: { diff: any }) {
  if (diff.binary)
    return (
      <div className="info-box">
        <Icon name="file" />
        이진 산출물입니다. 파일 버전과 해시를 확인하고 원본 제작 도구에서 시각
        검토해 주세요.
      </div>
    );
  const text = diff.diff || diff.content || "";
  return (
    <pre className="diff-content" tabIndex={0} aria-label="산출물 변경 내용">
      <code>
        {String(text)
          .split("\n")
          .map((line, i) => (
            <span
              className={
                line.startsWith("+") && !line.startsWith("+++")
                  ? "added"
                  : line.startsWith("-") && !line.startsWith("---")
                    ? "removed"
                    : line.startsWith("@@")
                      ? "hunk"
                      : ""
              }
              key={i}
            >
              <i aria-hidden="true">{i + 1}</i>
              {line || " "}
              {"\n"}
            </span>
          ))}
      </code>
    </pre>
  );
}

function artefactKind(path: string) {
  const ext = path.split(".").at(-1)?.toLowerCase();
  if (["docx", "pptx", "hwpx", "xlsx"].includes(ext || "")) return "편집 원본";
  if (ext === "pdf") return "PDF 출력";
  if (["png", "jpg", "jpeg", "webp"].includes(ext || ""))
    return "페이지·이미지";
  return "산출물·근거";
}
