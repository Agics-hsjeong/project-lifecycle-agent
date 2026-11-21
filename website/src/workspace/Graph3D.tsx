import React, { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { CanvasTexture, Sprite, SpriteMaterial, SRGBColorSpace } from "three";
import type { Selection } from "./model";

const escape = (text: string) =>
  text.replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ]!,
  );

/** Optional inspection view. No auto rotation; 2D and the connection list remain available. */
export default function Graph3D({
  elements,
  selection,
  depth,
  onSelect,
  fitTick = 0,
}: {
  elements: any[];
  selection: Selection | null;
  depth: number;
  fitTick?: number;
  onSelect: (value: Selection) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<any>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [size, setSize] = useState({ width: 800, height: 520 });
  const viewportSize = useRef(size);
  viewportSize.current = size;
  const data = useMemo(
    () => ({
      nodes: elements
        .filter((item) => !item.data.source)
        .map((item) => ({ ...item.data })),
      links: elements
        .filter((item) => item.data.source)
        .map((item) => ({ ...item.data })),
    }),
    [elements],
  );
  const labels = useMemo(() => new Map<string, Sprite>(), [data]);
  useEffect(
    () => () => {
      for (const sprite of labels.values()) {
        (sprite.material as SpriteMaterial).map?.dispose();
        (sprite.material as SpriteMaterial).dispose();
      }
    },
    [labels],
  );
  const spriteFor = (node: any) => {
    let sprite = labels.get(node.id);
    if (sprite) return sprite;
    const canvas = document.createElement("canvas");
    canvas.width = 768;
    canvas.height = 96;
    const context = canvas.getContext("2d")!;
    context.font = "32px Pretendard,sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillStyle = "#435967";
    const text = String(node.label || node.id);
    const lines = text.split("\n").slice(0, 2);
    lines.forEach((line, index) =>
      context.fillText(
        line.length > 28 ? line.slice(0, 27) + "…" : line,
        384,
        lines.length > 1 ? 28 + index * 38 : 48,
        740,
      ),
    );
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    sprite = new Sprite(
      new SpriteMaterial({
        map: texture,
        transparent: true,
        depthWrite: false,
        depthTest: false,
        sizeAttenuation: false,
      }),
    );
    sprite.renderOrder = 10;
    // Fixed screen size keeps task names readable at every graph distance.
    sprite.onBeforeRender = (_renderer, _scene, camera) => {
      const fov = Number((camera as any).fov || 75);
      const unit =
        (2 * Math.tan((fov * Math.PI) / 360)) /
        Math.max(180, viewportSize.current.height);
      sprite!.scale.set(320 * unit, 40 * unit, 1);
    };
    sprite.visible =
      (viewportSize.current.width >= 700 && node.kind === "task") ||
      node.id === selection?.id ||
      node.id === hovered;
    sprite.position.set(0, -12, 0);
    labels.set(node.id, sprite);
    return sprite;
  };
  const connected = useMemo(() => {
    const ids = new Set<string>();
    if (!selection) return ids;
    ids.add(selection.id);
    for (let step = 0; step < depth; step++) {
      const current = new Set(ids);
      for (const link of data.links) {
        const source =
          typeof link.source === "object" ? link.source.id : link.source;
        const target =
          typeof link.target === "object" ? link.target.id : link.target;
        if (current.has(source) || current.has(target)) {
          ids.add(source);
          ids.add(target);
        }
      }
    }
    return ids;
  }, [selection, depth, data]);
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setSize({
        width: Math.max(1, rect.width),
        height: Math.max(180, rect.height),
      });
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const fitGraph = (duration = 0) =>
    graph.current?.zoomToFit(duration, size.width < 700 ? 16 : 24);
  useEffect(() => {
    const timer = setTimeout(() => fitGraph(), 500);
    return () => clearTimeout(timer);
  }, [data, size.width, size.height]);
  useEffect(() => {
    if (fitTick) fitGraph(160);
  }, [fitTick]);
  useEffect(() => {
    for (const [id, sprite] of labels) {
      const node = data.nodes.find((n) => n.id === id);
      sprite.visible =
        (size.width >= 700 && node?.kind === "task") ||
        id === selection?.id ||
        id === hovered;
    }
  }, [selection?.id, hovered, labels, data, size.width]);
  const isRelated = (node: any) => !selection || connected.has(node.id);
  return (
    <div
      className="knowledge-3d"
      ref={container}
      role="img"
      aria-label="3D 작업 관계 탐색. 마우스로 회전하고 아래 연결 목록으로 선택할 수 있습니다."
    >
      <ForceGraph3D
        ref={graph}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="#f8fafb"
        showNavInfo={false}
        enableNodeDrag={false}
        onNodeHover={(node) => setHovered(node ? String(node.id) : null)}
        nodeLabel={(node) =>
          `<span style="font:13px Pretendard,sans-serif;color:#334e5c;background:#fff;padding:5px 7px;border:1px solid #dce5ea;border-radius:4px">${escape(String(node.fullLabel || node.label || node.id))}</span>`
        }
        nodeColor={(node) =>
          !isRelated(node)
            ? "#d5e0e5"
            : node.id === selection?.id
              ? "#087f82"
              : node.kind === "artefact"
                ? "#8eacbc"
                : node.kind === "agent"
                  ? "#7ba799"
                  : node.status === "awaiting_approval"
                    ? "#b29b72"
                    : "#81afa4"
        }
        nodeVal={(node) =>
          node.kind === "task" ? 5 : node.kind === "artefact" ? 2 : 3
        }
        nodeOpacity={0.95}
        nodeThreeObject={spriteFor}
        nodeThreeObjectExtend={true}
        linkOpacity={0.5}
        linkColor={(link) => {
          const source =
            typeof link.source === "object" ? link.source.id : link.source;
          const target =
            typeof link.target === "object" ? link.target.id : link.target;
          return !selection || (connected.has(source) && connected.has(target))
            ? "#6d9b96"
            : "#d6e0e6";
        }}
        linkWidth={(link) => {
          const source =
            typeof link.source === "object" ? link.source.id : link.source;
          return selection && source === selection.id ? 1.5 : 0.5;
        }}
        linkLabel={(link) => escape(String(link.label || ""))}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={0.88}
        onNodeClick={(node) =>
          onSelect({
            kind: node.kind as Selection["kind"],
            id: String(node.id),
          })
        }
        cooldownTicks={100}
        onEngineStop={() => {
          fitGraph(160);
        }}
      />
    </div>
  );
}
