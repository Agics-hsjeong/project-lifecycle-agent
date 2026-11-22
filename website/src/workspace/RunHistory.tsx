import React from "react";
import { labels } from "./model";
/** Run receipts stay visible even when their superseded steps leave the task board. */
export function RunHistory({ runs }: { runs: any[] }) {
  return runs.length ? (
    <section className="run-history" aria-label="보존된 실행 기록">
      <header>
        <h2>실행 기록</h2>
        <p>실패와 이전 시도도 원본 상태로 보존합니다.</p>
      </header>
      {runs.map((run, index) => (
        <details key={run.run_id || run.id || index}>
          <summary>
            <strong>{run.run_id || run.id || `실행 ${index + 1}`}</strong>
            <span>{run.title || "실행 계획"}</span>
            <b>
              {labels[run.status] ||
                run.status ||
                `${(run.steps || []).filter((s: any) => s.status === "failed").length} 실패 · ${(run.steps || []).length}개 단계`}
            </b>
          </summary>
          <ol>
            {(run.steps || []).map((step: any, i: number) => (
              <li key={step.id || step.step_id || i}>
                <code>
                  {step.task_id || step.step_id || step.id || `STEP ${i + 1}`}
                </code>
                <span>
                  {step.title || step.step_id || step.id || "단계 이름 미기록"}
                  {step.superseded && <small> · 이전 시도</small>}
                </span>
                <strong>
                  {step.valid === false ? "기준 변경 · " : ""}
                  {labels[step.status] || step.status || "상태 미보고"}
                </strong>
              </li>
            ))}
          </ol>
        </details>
      ))}
    </section>
  ) : null;
}
