import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  MiniMap,
  Handle,
  Position,
  ReactFlowProvider,
  useReactFlow,
  BaseEdge,
  type NodeProps,
  type EdgeProps,
} from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import cytoscape from "cytoscape";
const Graph3D = React.lazy(() => import("./Graph3D"));
import "@xyflow/react/dist/style.css";
import "./canvas-views.css";
import {
  PHASES,
  ROLE,
  labels,
  phaseOf,
  completed,
  awaiting,
  active,
  type Task,
  type Snapshot,
  type Selection,
} from "./model";
import { Icon, StatusBadge } from "./WorkspacePrimitives";
export { Icon, StatusBadge } from "./WorkspacePrimitives";

const CARD_WIDTH = 248;
const CARD_HEIGHT = 144;
const PHASE_GAP = 324;
const ROW_GAP = 184;
function TaskNode({ data, selected }: NodeProps) {
  const t = data.task as Task;
  return (
    <div
      className={`flow-card ${selected ? "is-selected" : ""} task-${t.status}`}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (["Enter", " "].includes(event.key)) {
          event.preventDefault();
          (data.onSelect as (s: Selection) => void)({ kind: "task", id: t.id });
        }
      }}
    >
      <Handle type="target" position={Position.Left} />
      <Handle type="target" position={Position.Top} id="top" />
      <Handle type="target" position={Position.Right} id="side-target" />
      <div className="flow-card-top">
        <span className="mono">{t.id}</span>
        <span
          className={`flow-state-dot state-${t.status}`}
          aria-hidden="true"
        />
      </div>
      <strong title={t.title}>{t.title}</strong>
      <div className="flow-card-role">
        <Icon name="agent" size={14} />
        {ROLE[t.role] || t.role || "담당 미정"}
      </div>
      <footer>
        <span className={`flow-status status-text-${t.status}`}>
          {labels[t.status] || t.status}
        </span>
        <span>
          {t.depends_on.length ? `선행 ${t.depends_on.length}` : "시작 작업"}
        </span>
      </footer>
      <Handle type="source" position={Position.Right} />
      <Handle type="source" position={Position.Bottom} id="bottom" />
    </div>
  );
}
function FeedbackEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  style,
  data,
}: EdgeProps) {
  const lane =
    Number(data?.routeY) || Math.max(sourceY, targetY) + CARD_HEIGHT / 2 + 24;
  const path = `M ${sourceX} ${sourceY} C ${sourceX + 34} ${sourceY}, ${sourceX + 44} ${sourceY + 24}, ${sourceX + 44} ${sourceY + 44} L ${sourceX + 44} ${lane - 16} Q ${sourceX + 44} ${lane} ${sourceX + 28} ${lane} L ${targetX + 60} ${lane} Q ${targetX + 44} ${lane} ${targetX + 44} ${lane - 16} L ${targetX + 44} ${targetY + 28} Q ${targetX + 44} ${targetY} ${targetX} ${targetY}`;
  return (
    <BaseEdge
      id={id}
      path={path}
      style={{
        ...style,
        stroke: "#a17c4b",
        strokeWidth: 1.25,
        strokeDasharray: "5 5",
      }}
    />
  );
}
function PhaseNode({ data }: NodeProps) {
  return (
    <div className="canvas-phase-node">
      <span>{String(data.id)}</span>
      <strong>{String(data.title)}</strong>
      <small>{String(data.count)}개 작업</small>
    </div>
  );
}
const nodeTypes = { task: TaskNode, phase: PhaseNode };
const edgeTypes = { feedback: FeedbackEdge };
const elk = new ELK();
function WorkflowInner({
  snapshot,
  tasks,
  selection,
  onSelect,
  focusTick,
  toolbar,
}: {
  snapshot: Snapshot;
  tasks: Task[];
  selection: Selection | null;
  onSelect: (s: Selection) => void;
  focusTick: number;
  toolbar?: React.ReactNode;
}) {
  const phases = PHASES.filter((p) => tasks.some((t) => phaseOf(t) === p.id));
  const phaseX = (id: string) =>
    Math.max(
      0,
      phases.findIndex((p) => p.id === id),
    ) *
      PHASE_GAP +
    28;
  const fallback = () =>
    Object.fromEntries(
      tasks.map((t) => [
        t.id,
        {
          x: phaseX(phaseOf(t)),
          y:
            76 +
            tasks.filter((n) => phaseOf(n) === phaseOf(t)).indexOf(t) * ROW_GAP,
        },
      ]),
    );
  const [positions, setPositions] =
    useState<Record<string, { x: number; y: number }>>(fallback);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: 500 });
  const { fitView, setCenter, setViewport, zoomIn, zoomOut } = useReactFlow();
  const topology = tasks
    .map((t) => t.id + ":" + phaseOf(t) + ":" + t.depends_on.join(","))
    .join("|");
  useEffect(() => {
    if (!canvasRef.current) return;
    const resize = new ResizeObserver(([entry]) =>
      setSize({
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      }),
    );
    resize.observe(canvasRef.current);
    return () => resize.disconnect();
  }, []);
  useEffect(() => {
    let cancelled = false;
    const ids = new Set(tasks.map((t) => t.id));
    elk
      .layout({
        id: "root",
        layoutOptions: {
          "elk.algorithm": "layered",
          "elk.direction": "RIGHT",
          "elk.spacing.nodeNode": "40",
          "elk.layered.spacing.nodeNodeBetweenLayers": "76",
          "elk.partitioning.activate": "true",
        },
        children: tasks.map((t) => ({
          id: t.id,
          width: CARD_WIDTH,
          height: CARD_HEIGHT,
          layoutOptions: {
            "elk.partitioning.partition": String(Number(phaseOf(t))),
          },
        })),
        edges: tasks.flatMap((t) =>
          t.depends_on
            .filter((d) => ids.has(d))
            .map((d) => ({
              id: d + ":" + t.id,
              sources: [d],
              targets: [t.id],
            })),
        ),
      })
      .then((graph) => {
        if (cancelled) return;
        const result: Record<string, { x: number; y: number }> = {};
        const elkY = new Map(graph.children?.map((c) => [c.id, c.y || 0]));
        for (const phase of phases) {
          const inPhase = tasks.filter((t) => phaseOf(t) === phase.id);
          const level = (t: Task, seen = new Set<string>()): number => {
            if (seen.has(t.id)) return 0;
            const visited = new Set(seen);
            visited.add(t.id);
            const parents = inPhase.filter((p) => t.depends_on.includes(p.id));
            return parents.length
              ? 1 + Math.max(...parents.map((p) => level(p, visited)))
              : 0;
          };
          inPhase
            .sort(
              (a, b) =>
                level(a) - level(b) ||
                (elkY.get(a.id) || 0) - (elkY.get(b.id) || 0) ||
                a.id.localeCompare(b.id),
            )
            .forEach(
              (t, i) =>
                (result[t.id] = { x: phaseX(phase.id), y: 76 + i * ROW_GAP }),
            );
        }
        setPositions(result);
      })
      .catch(() => {
        if (!cancelled) setPositions(fallback());
      });
    return () => {
      cancelled = true;
    };
  }, [topology]);
  useEffect(() => {
    const current =
      selection?.kind === "task"
        ? tasks.find((t) => t.id === selection.id)
        : tasks.find(active) || tasks.find(awaiting) || tasks[0];
    if (
      !current ||
      !positions[current.id] ||
      size.width < 10 ||
      size.height < 10
    )
      return;
    const timer = setTimeout(() => {
      if (!selection && tasks.length <= 6 && phases.length <= 2) {
        const values = Object.values(positions);
        const minX = Math.min(...values.map((p) => p.x)) - 24,
          maxX = Math.max(...values.map((p) => p.x)) + CARD_WIDTH + 24,
          maxY = Math.max(...values.map((p) => p.y)) + CARD_HEIGHT + 36;
        const zoom = Math.min(
          1,
          (size.width - 36) / (maxX - minX),
          (size.height - 25) / maxY,
        );
        setViewport(
          {
            x: (size.width - (maxX - minX) * zoom) / 2 - minX * zoom,
            y: 16,
            zoom: Math.max(0.82, zoom),
          },
          { duration: 0 },
        );
      } else {
        const p = positions[current.id];
        setCenter(p.x + CARD_WIDTH / 2, p.y + CARD_HEIGHT / 2, {
          zoom: 1,
          duration: window.matchMedia("(prefers-reduced-motion: reduce)")
            .matches
            ? 0
            : 220,
        });
      }
    }, 80);
    return () => clearTimeout(timer);
  }, [selection?.id, focusTick, positions, size.width, size.height, topology]);
  const ids = new Set(tasks.map((t) => t.id));
  const phaseNodes = phases.map((p) => ({
    id: "phase:" + p.id,
    type: "phase",
    position: { x: phaseX(p.id), y: 20 },
    data: {
      id: p.id,
      title: p.title,
      count: tasks.filter((t) => phaseOf(t) === p.id).length,
    },
    initialWidth: CARD_WIDTH,
    initialHeight: 38,
    selectable: false,
    draggable: false,
  }));
  const nodes: any[] = [
    ...phaseNodes,
    ...tasks.map((t) => ({
      id: t.id,
      type: "task",
      position: positions[t.id] || fallback()[t.id],
      data: { task: t, onSelect },
      initialWidth: CARD_WIDTH,
      initialHeight: CARD_HEIGHT,
      selected: selection?.id === t.id,
      ariaLabel: `${t.id} ${t.title} ${ROLE[t.role] || t.role || "담당 미정"} ${labels[t.status] || t.status} ${t.depends_on.length ? `선행 ${t.depends_on.length}` : "시작 작업"}`,
    })),
  ];
  const edges: any[] = tasks.flatMap((t) =>
    t.depends_on
      .filter((d) => ids.has(d))
      .map((d) => {
        const same = positions[d]?.x === positions[t.id]?.x;
        return {
          id: `dep:${d}:${t.id}`,
          source: d,
          target: t.id,
          sourceHandle: same ? "bottom" : undefined,
          targetHandle: same ? "top" : undefined,
          type: "smoothstep",
          pathOptions: { borderRadius: 12, offset: 24 },
          style: {
            stroke:
              selection?.id === t.id || selection?.id === d
                ? "#15827e"
                : "#b0bec6",
            strokeWidth: 1.25,
          },
          markerEnd: {
            type: "arrowclosed" as any,
            color: "#8da0aa",
            width: 14,
            height: 14,
          },
        };
      }),
  );
  const feedback = new Set<string>();
  const feedbackY =
    Math.max(0, ...Object.values(positions).map((p) => p.y)) + CARD_HEIGHT + 28;
  for (const item of snapshot.feedback || []) {
    if (ids.has(item.source) && ids.has(item.target)) {
      const key = item.source + ":" + item.target;
      if (!feedback.has(key)) {
        feedback.add(key);
        edges.push({
          id: "feedback:" + key,
          source: item.source,
          target: item.target,
          type: "feedback",
          targetHandle: "side-target",
          data: { routeY: feedbackY + feedback.size * 14 },
        });
      }
    }
  }
  for (const t of tasks) {
    if (
      t.revision_of &&
      ids.has(t.revision_of) &&
      !feedback.has(t.id + ":" + t.revision_of)
    )
      edges.push({
        id: "feedback:" + t.id,
        source: t.id,
        target: t.revision_of,
        type: "feedback",
        targetHandle: "side-target",
        data: { routeY: feedbackY + feedback.size * 14 },
      });
  }
  return (
    <section
      className="scene-panel workflow-scene"
      aria-label="작업 흐름 캔버스"
    >
      <div className="scene-toolbar">
        <div className="scene-meta">
          <strong>작업 흐름</strong>
          <span>{tasks.length}개 작업</span>
          <span>{edges.length}개 연결</span>
        </div>
        <div className="scene-controls">
          {toolbar}
          <button aria-label="워크플로우 축소" onClick={() => zoomOut()}>
            <span aria-hidden="true">−</span>
          </button>
          <button aria-label="워크플로우 확대" onClick={() => zoomIn()}>
            <span aria-hidden="true">+</span>
          </button>
          <button
            onClick={() =>
              fitView({
                padding: 0.15,
                minZoom: 0.35,
                maxZoom: 1,
                duration: 200,
              })
            }
          >
            <Icon name="expand" size={15} />
            전체 보기
          </button>
        </div>
      </div>
      <div className="scene-content workflow-canvas" ref={canvasRef}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={(_, n) => {
            if (n.type === "task") onSelect({ kind: "task", id: n.id });
          }}
          defaultViewport={{ x: 20, y: 18, zoom: 1 }}
          minZoom={0.3}
          maxZoom={1.6}
          nodesFocusable={false}
          nodesConnectable={false}
          nodesDraggable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#d8e1e7" gap={28} size={0.7} />
          <MiniMap
            nodeColor={(n) => {
              const t = n.data.task as Task;
              return !t
                ? "#d9e2e7"
                : active(t)
                  ? "#73b8a8"
                  : completed(t)
                    ? "#bacbc7"
                    : "#dbe2e7";
            }}
            maskColor="rgba(242,246,248,.72)"
            pannable
            zoomable
          />
        </ReactFlow>
      </div>
      <div className="scene-footer">
        <span>
          <i className="scene-line" />
          선행 작업
        </span>
        <span>
          <i className="scene-line is-feedback" />
          수정 전달
        </span>
        <small>노드를 선택해 실제 활동과 산출물을 확인하세요.</small>
      </div>
    </section>
  );
}
export function Workflow(props: React.ComponentProps<typeof WorkflowInner>) {
  return (
    <ReactFlowProvider>
      <WorkflowInner {...props} />
    </ReactFlowProvider>
  );
}
export function TaskList({
  tasks,
  selection,
  onSelect,
}: {
  tasks: Task[];
  selection: Selection | null;
  onSelect: (s: Selection) => void;
}) {
  return (
    <div className="task-list">
      {tasks.map((t) => (
        <button
          key={t.id}
          onClick={() => onSelect({ kind: "task", id: t.id })}
          className={selection?.id === t.id ? "selected" : ""}
        >
          <span className="mono list-id">{t.id}</span>
          <span>
            <strong>{t.title}</strong>
            <small>{ROLE[t.role] || t.role}</small>
          </span>
          <StatusBadge status={t.status} />
          <Icon name="chevron" size={16} />
        </button>
      ))}
      {!tasks.length && (
        <Empty
          text="표시할 작업이 없습니다"
          detail="필터를 바꾸거나 프로젝트에 작업을 등록해 주세요."
        />
      )}
    </div>
  );
}
export function Empty({ text, detail }: { text: string; detail: string }) {
  return (
    <div className="empty-state">
      <Icon name="workflow" size={32} />
      <strong>{text}</strong>
      <p>{detail}</p>
    </div>
  );
}
const relationType = (value: unknown) => {
  const raw = String(value || "references");
  const normalized = raw.toLowerCase().replace(/[ -]+/g, "_");
  return [
    "depends_on",
    "assigned_to",
    "references",
    "produces",
    "validates",
    "feedback",
  ].includes(normalized)
    ? normalized
    : raw;
};
const relationLabel = (value: string) =>
  ({
    depends_on: "선행",
    assigned_to: "담당",
    references: "참조",
    produces: "생성",
    validates: "검증",
    feedback: "수정 전달",
  })[value] || value;
export function KnowledgeMap({
  snapshot,
  tasks,
  selection,
  onSelect,
  toolbar,
}: {
  snapshot: Snapshot;
  tasks: Task[];
  selection: Selection | null;
  onSelect: (s: Selection) => void;
  toolbar?: React.ReactNode;
}) {
  const container = useRef<HTMLDivElement>(null),
    cy = useRef<any>(null);
  const previousFocus = useRef<string | null>(null);
  const camera = useRef<{
    key: string;
    zoom: number;
    pan: { x: number; y: number };
  } | null>(null);
  const [depth, setDepth] = useState(1),
    [dimension, setDimension] = useState("2d"),
    [fit3dTick, setFit3dTick] = useState(0),
    [allTasks, setAllTasks] = useState(false),
    [showArtifacts, setShowArtifacts] = useState(true),
    [showAgents, setShowAgents] = useState(true);
  const elements = useMemo(() => {
    const nodes: any[] = [],
      edges: any[] = [],
      known = new Set<string>();
    const add = (
      id: string,
      kind: string,
      label: string,
      status = "",
      fullLabel = label,
    ) => {
      if (!known.has(id)) {
        nodes.push({ data: { id, kind, label, status, fullLabel } });
        known.add(id);
      }
    };
    const edge = (source: string, target: string, type: string) => {
      const kind = relationType(type);
      edges.push({
        data: {
          id: `edge:${edges.length}:${source}:${target}`,
          source,
          target,
          type: kind,
          label: relationLabel(kind),
        },
      });
    };
    const phase = PHASES.find((p) =>
      p.stages.includes(snapshot.project.current_stage),
    )?.id;
    const core = tasks.filter(
      (t) =>
        phaseOf(t) === phase ||
        t.id === selection?.id ||
        t.artefact_ids.includes(selection?.id || ""),
    );
    const scope = new Set(
      core.flatMap((t) => [
        t.id,
        ...t.depends_on,
        ...tasks.filter((n) => n.depends_on.includes(t.id)).map((n) => n.id),
      ]),
    );
    const visible =
      allTasks || !core.length ? tasks : tasks.filter((t) => scope.has(t.id));
    visible.forEach((t) => add(t.id, "task", t.title, t.status));
    if (showArtifacts)
      snapshot.artefacts
        .filter((a) => visible.some((t) => t.artefact_ids.includes(a.id)))
        .forEach((a) => {
          const parts = a.path.split("/");
          const name = parts.at(-1) || a.path,
            parent = parts.at(-2) || "";
          const context =
            parent.length > 20
              ? parent.slice(0, 10) + "…" + parent.slice(-5)
              : parent;
          add(
            a.id,
            "artefact",
            name + (context ? "\n" + context : ""),
            "",
            a.path,
          );
        });
    if (showAgents)
      snapshot.agents
        .filter(
          (a) =>
            (a.task_id && visible.some((t) => t.id === a.task_id)) ||
            snapshot.relations.some(
              (r: any) =>
                (r.source === a.id || r.target === a.id) &&
                visible.some((t) => t.id === r.source || t.id === r.target),
            ),
        )
        .forEach((a) => {
          const role = ROLE[a.role] || a.role || "실행자";
          const provider =
            a.provider === "codex"
              ? "Codex"
              : a.provider === "claude"
                ? "Claude Code"
                : "호스트 미보고";
          add(
            a.id,
            "agent",
            `${role}\n${provider} · ${String(a.id)
              .replace(/^(?:agent:)?runner:/i, "")
              .replace(/^RUNNER-/i, "")
              .slice(0, 6)}`,
            "",
            `${role} · ${provider} · ${a.id}`,
          );
          if (a.task_id) edge(a.id, a.task_id, "assigned_to");
        });
    visible.forEach((t) => {
      t.depends_on.forEach((id) => edge(id, t.id, "depends_on"));
      if (showArtifacts)
        t.artefact_ids.forEach((id) => {
          if (
            !snapshot.relations.some(
              (r: any) =>
                (r.source || r.from) === t.id && (r.target || r.to) === id,
            )
          )
            edge(t.id, id, "references");
        });
      t.requirement_ids.forEach((id) => {
        add(id, "requirement", id);
        edge(id, t.id, "references");
      });
    });
    snapshot.relations.forEach((r: any) =>
      edge(
        r.source || r.from,
        r.target || r.to,
        r.type || r.label || "references",
      ),
    );
    const pairs = new Set<string>();
    const links = edges.filter((e) => {
      const d = e.data,
        key = JSON.stringify([d.source, d.target, d.type]);
      if (!known.has(d.source) || !known.has(d.target) || pairs.has(key))
        return false;
      pairs.add(key);
      return true;
    });
    const centers = new Map(visible.map((t, i) => [t.id, i * 106]));
    const taskCenter = visible.length ? (visible.length - 1) * 53 : 0;
    const columns = ["requirement", "task", "artefact", "agent"];
    let columnX = 100;
    for (const kind of columns) {
      const members = nodes.filter((n) => n.data.kind === kind);
      const anchor = (n: any) => {
        if (kind === "task") return centers.get(n.data.id) || 0;
        const adjacent = links
          .filter(
            (e) => e.data.source === n.data.id || e.data.target === n.data.id,
          )
          .map((e) =>
            e.data.source === n.data.id ? e.data.target : e.data.source,
          )
          .filter((id) => centers.has(id));
        return adjacent.length
          ? adjacent.reduce((sum, id) => sum + (centers.get(id) || 0), 0) /
              adjacent.length
          : taskCenter;
      };
      members.sort(
        (a, b) =>
          anchor(a) - anchor(b) ||
          String(a.data.id).localeCompare(String(b.data.id)),
      );
      // Keep file-heavy projects compact without shrinking every label to fit.
      const rows =
        kind === "artefact"
          ? Math.max(6, visible.length)
          : Math.max(1, members.length);
      const subcolumns = Math.max(1, Math.ceil(members.length / rows));
      for (let column = 0; column < subcolumns; column++) {
        const group = members.slice(column * rows, (column + 1) * rows);
        let next = -Infinity;
        const targets = group.map((n) => {
          const y = Math.max(anchor(n), next);
          next = y + 84;
          return y;
        });
        const midpoint = targets.length
          ? (targets[0] + targets.at(-1)!) / 2
          : 0;
        const offset = kind === "task" ? 0 : taskCenter - midpoint;
        group.forEach(
          (n, i) =>
            (n.position = {
              x: columnX + column * 224,
              y: 76 + targets[i] + offset,
            }),
        );
      }
      columnX += subcolumns * 224;
    }
    return [...nodes, ...links];
  }, [
    JSON.stringify({
      tasks: tasks.map((t) => ({
        id: t.id,
        title: t.title,
        status: t.status,
        phase: t.phase,
        stage: t.stage,
        depends_on: t.depends_on,
        artefact_ids: t.artefact_ids,
        requirement_ids: t.requirement_ids,
      })),
      artefacts: snapshot.artefacts.map((a) => ({ id: a.id, path: a.path })),
      agents: snapshot.agents.map((a) => ({
        id: a.id,
        name: a.name,
        role: a.role,
        provider: a.provider,
        task_id: a.task_id,
      })),
      relations: snapshot.relations,
      stage: snapshot.project.current_stage,
    }),
    showArtifacts,
    showAgents,
    allTasks,
    selection?.id,
  ]);
  const elementKey = JSON.stringify(elements);
  const geometryKey = JSON.stringify(
    elements.map((e) =>
      e.data.source
        ? [e.data.source, e.data.target, e.data.type]
        : [e.data.id, e.data.kind, e.position],
    ),
  );
  useEffect(() => {
    if (!container.current || dimension !== "2d") return;
    const graph = cytoscape({
      container: container.current,
      elements,
      layout: { name: "preset", fit: false } as any,
      minZoom: 0.15,
      maxZoom: 1.6,
      wheelSensitivity: 0.18,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-family": "Pretendard, sans-serif",
            "font-size": 13,
            "font-weight": 500,
            color: "#293f4c",
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": "140px",
            "text-overflow-wrap": "anywhere",
            "text-margin-y": 0,
            "background-color": "#ffffff",
            "border-width": 1,
            "border-color": "#ccd8df",
            shape: "round-rectangle",
            width: 170,
            height: 62,
            "overlay-opacity": 0,
          },
        },
        {
          selector: 'node[kind="task"]',
          style: { "font-weight": 600, width: 174, height: 66 },
        },
        {
          selector: 'node[kind="artefact"]',
          style: { "background-color": "#f4f7f9", height: 52, "font-size": 12 },
        },
        {
          selector: 'node[kind="agent"]',
          style: {
            "background-color": "#eff7f5",
            "border-color": "#c8ded8",
            height: 58,
            "font-size": 12,
          },
        },
        {
          selector: 'node[kind="requirement"]',
          style: {
            "background-color": "#f1f4f8",
            width: 142,
            height: 46,
            "font-size": 12,
          },
        },
        {
          selector: 'node[status="running"],node[status="doing"]',
          style: {
            "background-color": "#eaf6f2",
            "border-color": "#549d8e",
            "border-width": 1.5,
          },
        },
        {
          selector:
            'node[status="failed"],node[status="blocked"],node[status="stale"]',
          style: { "border-color": "#b9956d" },
        },
        {
          selector: "edge",
          style: {
            width: 1,
            "line-color": "#c2cdd4",
            "target-arrow-color": "#a7b7c0",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.65,
            "curve-style": "bezier",
            "font-size": 12,
            color: "#526875",
            "text-background-color": "#fff",
            "text-background-opacity": 1,
            "text-background-padding": "3px",
            label: "",
          },
        },
        {
          selector: ".focused",
          style: { "border-color": "#087f82", "border-width": 2 },
        },
        {
          selector: "edge.related",
          style: {
            label: "data(label)",
            "line-color": "#609e97",
            "target-arrow-color": "#609e97",
            width: 1.6,
          },
        },
        { selector: ".dimmed", style: { opacity: 0.36 } },
      ],
    });
    const initial = tasks.find(active) || tasks.find(awaiting) || tasks[0];
    const focus = initial && graph.getElementById(initial.id);
    if (camera.current?.key === geometryKey) {
      graph.viewport({ zoom: camera.current.zoom, pan: camera.current.pan });
    } else {
      graph.fit(undefined, graph.width() < 700 ? 16 : 36);
      const fitZoom = graph.zoom();
      if (graph.width() < 700 || fitZoom >= 0.72) {
        graph.zoom(Math.min(1, fitZoom));
        graph.center();
      } else {
        graph.zoom(0.95);
        if (focus?.length) graph.center(focus);
        else graph.center();
      }
    }
    container.current.dataset.zoom = String(graph.zoom());
    graph.on("zoom", () => {
      if (container.current)
        container.current.dataset.zoom = String(graph.zoom());
    });
    cy.current = graph;
    graph.on("tap", "node", (event) =>
      onSelect({ kind: event.target.data("kind"), id: event.target.id() }),
    );
    const resize = new ResizeObserver(() => graph.resize());
    resize.observe(container.current);
    return () => {
      resize.disconnect();
      camera.current = {
        key: geometryKey,
        zoom: graph.zoom(),
        pan: { ...graph.pan() },
      };
      graph.destroy();
      cy.current = null;
    };
  }, [elementKey, dimension]);
  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    graph.elements().removeClass("dimmed focused related");
    if (!selection) {
      previousFocus.current = null;
      return;
    }
    const selected = graph.getElementById(selection.id);
    if (!selected.length) return;
    let connected = selected.closedNeighborhood();
    if (depth === 2) connected = connected.closedNeighborhood();
    graph.elements().difference(connected).addClass("dimmed");
    connected.edges().addClass("related");
    selected.addClass("focused");
    const nextFocus = `${selection.id}:${depth}:${geometryKey}:${dimension}`;
    if (previousFocus.current === nextFocus) return;
    previousFocus.current = nextFocus;
    graph.animate({
      center: { eles: graph.width() < 700 ? selected : connected },
      ...(graph.width() < 700 ? { zoom: 0.95 } : {}),
      duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? 0
        : 180,
    });
  }, [selection?.id, depth, elementKey, dimension]);
  const nodeCount = elements.filter((e) => !e.data.source).length,
    edgeCount = elements.length - nodeCount;
  return (
    <section className="scene-panel map-scene" aria-label="작업 관계 지도">
      <div className="scene-toolbar">
        <div className="scene-meta">
          <strong>연결된 작업</strong>
          <span>{nodeCount}개 항목</span>
          <span>{edgeCount}개 관계</span>
        </div>
        <div className="scene-controls">
          {toolbar}
          <div className="scene-switch" aria-label="작업 지도 표시 방식">
            <button
              className={dimension === "2d" ? "active" : ""}
              aria-pressed={dimension === "2d"}
              onClick={() => setDimension("2d")}
            >
              2D
            </button>
            <button
              className={dimension === "3d" ? "active" : ""}
              aria-pressed={dimension === "3d"}
              onClick={() => setDimension("3d")}
            >
              3D 탐색
            </button>
          </div>
          <button
            aria-label="작업 지도 축소"
            disabled={dimension !== "2d"}
            onClick={() => {
              const graph = cy.current;
              if (graph)
                graph.zoom({
                  level: Math.max(graph.minZoom(), graph.zoom() / 1.25),
                  renderedPosition: {
                    x: graph.width() / 2,
                    y: graph.height() / 2,
                  },
                });
            }}
          >
            <span aria-hidden="true">−</span>
          </button>
          <button
            aria-label="작업 지도 확대"
            disabled={dimension !== "2d"}
            onClick={() => {
              const graph = cy.current;
              if (graph)
                graph.zoom({
                  level: Math.min(graph.maxZoom(), graph.zoom() * 1.25),
                  renderedPosition: {
                    x: graph.width() / 2,
                    y: graph.height() / 2,
                  },
                });
            }}
          >
            <span aria-hidden="true">+</span>
          </button>
          <button
            aria-label="작업 지도 전체 맞춤"
            onClick={() => {
              if (dimension === "3d") {
                setFit3dTick((t) => t + 1);
                return;
              }
              const graph = cy.current;
              if (graph) graph.fit(undefined, graph.width() < 700 ? 16 : 36);
            }}
          >
            <Icon name="expand" size={16} />
          </button>
        </div>
      </div>
      <div className="map-scene-options">
        <div className="scene-switch" aria-label="관계 강조 범위">
          <button
            className={depth === 1 ? "active" : ""}
            aria-pressed={depth === 1}
            onClick={() => setDepth(1)}
          >
            직접 연결
          </button>
          <button
            className={depth === 2 ? "active" : ""}
            aria-pressed={depth === 2}
            onClick={() => setDepth(2)}
          >
            2단계 확장
          </button>
        </div>
        <label>
          <input
            type="checkbox"
            checked={allTasks}
            onChange={(e) => setAllTasks(e.target.checked)}
          />
          전체 작업
        </label>
        <label>
          <input
            type="checkbox"
            checked={showArtifacts}
            onChange={(e) => setShowArtifacts(e.target.checked)}
          />
          산출물
        </label>
        <label>
          <input
            type="checkbox"
            checked={showAgents}
            onChange={(e) => setShowAgents(e.target.checked)}
          />
          에이전트
        </label>
        <span>항목을 선택하면 실제 연결을 강조합니다.</span>
      </div>
      <div className="scene-content map-scene-content">
        {dimension === "3d" ? (
          <GraphBoundary onFallback={() => setDimension("2d")}>
            <React.Suspense
              fallback={
                <p className="muted">3D 작업 지도를 불러오고 있습니다.</p>
              }
            >
              <Graph3D
                elements={elements}
                selection={selection}
                depth={depth}
                fitTick={fit3dTick}
                onSelect={onSelect}
              />
            </React.Suspense>
          </GraphBoundary>
        ) : (
          <div
            ref={container}
            className="knowledge-canvas"
            role="img"
            aria-label="작업·산출물·실행자 연결 지도. 아래 연결 목록으로 키보드 탐색이 가능합니다."
          />
        )}
      </div>
      <div className="scene-footer map-legend">
        <span>
          <i className="map-node-key kind-requirement" />
          요구사항
        </span>
        <span>
          <i className="map-node-key kind-task" />
          작업
        </span>
        <span>
          <i className="map-node-key kind-artefact" />
          산출물
        </span>
        <span>
          <i className="map-node-key kind-agent" />
          실행자
        </span>
        <small>명시된 연결만 표시</small>
      </div>
      <details className="graph-accessible">
        <summary>
          연결 목록으로 탐색 <span>{nodeCount}</span>
        </summary>
        <div>
          {elements
            .filter((e) => !e.data.source)
            .map((e) => (
              <button
                key={e.data.id}
                onClick={() => onSelect({ kind: e.data.kind, id: e.data.id })}
              >
                {e.data.fullLabel || e.data.label}
              </button>
            ))}
        </div>
      </details>
    </section>
  );
}
export { Wbs } from "./WbsView";

class GraphBoundary extends React.Component<
  { children: React.ReactNode; onFallback: () => void },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="empty-state">
        <strong>이 환경에서 3D 화면을 열 수 없습니다.</strong>
        <p>2D 지도와 연결 목록에서 같은 관계를 탐색할 수 있습니다.</p>
        <button className="quiet-button" onClick={this.props.onFallback}>
          2D 지도로 돌아가기
        </button>
      </div>
    ) : (
      this.props.children
    );
  }
}
