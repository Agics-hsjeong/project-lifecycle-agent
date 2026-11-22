import React from "react";
import { labels } from "./model";

function failureCount(run: any) {
  return (run.steps || []).reduce(
    (total: number, step: any) =>
      total +
      (step.result_history || []).filter((row: any) => row.status === "failed")
        .length,
    0,
  );
}
function attemptLabel(row: any) {
  return row.attempt == null ? "차수 미보고" : `${row.attempt}차`;
}
/** Historical failures stay visible after the last attempt succeeds. */
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
              {failureCount(run) > 0
                ? `실패 기록 ${failureCount(run)}건 · ${(run.steps || []).length}개 단계`
                : labels[run.status] ||
                  run.status ||
                  `${(run.steps || []).filter((s: any) => s.status === "failed").length} 실패 · ${(run.steps || []).length}개 단계`}
            </b>
          </summary>
          <ol>
            {(run.steps || []).map((step: any, i: number) => (
              <li
                key={step.id || step.step_id || i}
                className="run-attempt-step"
              >
                <code>
                  {step.task_id || step.step_id || step.id || `STEP ${i + 1}`}
                </code>
                <span>
                  {step.title || step.step_id || step.id || "단계 이름 미기록"}
                  {step.superseded && <small> · 이전 시도</small>}
                  {step.attempts != null && (
                    <small> · 실행 {step.attempts}회</small>
                  )}
                </span>
                <strong>
                  {step.valid === false ? "기준 변경 · " : ""}
                  {labels[step.status] || step.status || "상태 미보고"}
                </strong>
                {(step.result_history || []).length > 0 && (
                  <ol
                    className="run-attempt-results"
                    aria-label={`${step.title || step.id} 시도별 원장 기록`}
                  >
                    {step.result_history.map((row: any) => (
                      <li key={row.id} data-result-status={row.status}>
                        <span>
                          {attemptLabel(row)} ·{" "}
                          {labels[row.status] || row.status}
                        </span>
                        <p>{row.summary || "설명 미보고"}</p>
                      </li>
                    ))}
                  </ol>
                )}
                {step.history_omitted > 0 && (
                  <p className="run-attempt-omitted">
                    이전 기록 {step.history_omitted}건은 API 표시 범위 밖입니다.
                  </p>
                )}
              </li>
            ))}
          </ol>
        </details>
      ))}
    </section>
  ) : null;
}
