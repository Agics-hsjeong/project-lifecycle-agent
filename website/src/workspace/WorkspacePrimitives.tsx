import React from "react";
import { labels, active, executionLabel, type Task } from "./model";

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const shapes: Record<string, React.ReactNode> = {
    workflow: (
      <>
        <rect x="3" y="3" width="6" height="6" rx="1.5" />
        <rect x="15" y="15" width="6" height="6" rx="1.5" />
        <path d="M6 9v9h9M9 6h9v9" />
      </>
    ),
    map: (
      <>
        <circle cx="12" cy="12" r="3" />
        <circle cx="4" cy="5" r="2" />
        <circle cx="20" cy="5" r="2" />
        <circle cx="6" cy="20" r="2" />
        <circle cx="20" cy="20" r="2" />
        <path d="m6 6 4 4m4 0 4-4m-4 8 4 4M10 14l-3 4" />
      </>
    ),
    wbs: (
      <>
        <path d="M3 5h6M3 12h6M3 19h6" />
        <rect x="12" y="3" width="8" height="4" rx="1" />
        <rect x="10" y="10" width="7" height="4" rx="1" />
        <rect x="15" y="17" width="6" height="4" rx="1" />
      </>
    ),
    search: (
      <>
        <circle cx="10.5" cy="10.5" r="6.5" />
        <path d="m16 16 5 5" />
      </>
    ),
    filter: <path d="M3 5h18l-7 8v6l-4-2v-4z" />,
    layers: (
      <>
        <path d="m12 3 10 6-10 6L2 9zM2 13l10 6 10-6M2 17l10 6 10-6" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    chevron: <path d="m9 5 7 7-7 7" />,
    file: (
      <>
        <path d="M13 3H5v18h14V9zM13 3v6h6M8 13h8M8 17h5" />
      </>
    ),
    agent: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 21v-3a5 5 0 0 1 5-5h6a5 5 0 0 1 5 5v3" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 6v6l4 2" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 11a8 8 0 0 0-14-5L3 9M3 3v6h6M4 13a8 8 0 0 0 14 5l3-3m0 6v-6h-6" />
      </>
    ),
    shield: (
      <>
        <path d="m12 3 8 3v5c0 5-4 8-8 10-4-2-8-5-8-10V6z" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
    expand: (
      <>
        <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />
      </>
    ),
    log: (
      <>
        <path d="M4 5h16M4 12h16M4 19h10" />
      </>
    ),
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v6m0-11v2" />
      </>
    ),
    download: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {shapes[name] || shapes.info}
    </svg>
  );
}
export function StatusBadge({ status, task }: { status: string; task?: Task }) {
  const runtime = task ? executionLabel(task) : null;
  if (task && !active(task) && ["doing", "running"].includes(status))
    status = "todo";
  return (
    <span className={`status status-${status}`}>
      <span aria-hidden="true">
        {["done", "succeeded", "approved"].includes(status)
          ? "✓"
          : ["running", "doing"].includes(status)
            ? "◌"
            : ["blocked", "failed", "revision_requested", "stale"].includes(
                  status,
                )
              ? "!"
              : "·"}
      </span>
      {runtime || labels[status] || status}
    </span>
  );
}
