import React, { useMemo } from "react";
import { ROLE, type Snapshot, type Selection } from "./model";
import {
  buildRepairFlows,
  type RepairFlow as Flow,
  type RepairStage,
} from "./repair-flow";
import "./repair-flow.css";

function stageLabel(stage: RepairStage) {
  const attempt = stage.attempt == null ? "차수 미보고" : `${stage.attempt}차`;
  if (stage.kind === "repair") return `개발 수정 · ${attempt}`;
  return `QA ${attempt}${stage.kind === "recheck" ? " 재검증" : ""}`;
}
function statusLabel(stage: RepairStage) {
  if (stage.status === "succeeded") {
    if (!stage.is_latest) return "당시 완료";
    if (stage.valid === false) return "근거 변경";
    if (stage.valid !== true) return "통과 기록";
    return stage.kind === "repair" ? "수정 완료" : "통과";
  }
  return (
    (
      {
        failed: "실패",
        running: "진행 중",
        blocked: "차단",
        awaiting_decision: "판단 대기",
        skipped: "적용 제외",
      } as Record<string, string>
    )[stage.status] || "상태 미보고"
  );
}
function stageTone(stage: RepairStage) {
  if (stage.status === "failed") return "failed";
  if (stage.status === "running") return "running";
  if (stage.status === "succeeded" && stage.is_latest && stage.valid === true)
    return "passed";
  return "neutral";
}
function flowLabel(flow: Flow) {
  if (
    flow.stages.some(
      (stage) =>
        stage.kind === "repair" &&
        stage.is_latest &&
        stage.current_status === "running",
    )
  )
    return "개발 수정 중";
  return {
    passed: "재검증 통과",
    running: "QA 재검증 중",
    failed: "QA 실패",
    stale: "근거 변경 · 재검증 필요",
    unresolved: "실행 기록 확인 필요",
  }[flow.state];
}
function recordedTime(stage: RepairStage) {
  if (!stage.at) return "기록 시각 미보고";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(stage.at));
}

/** A visible account of failure and repair, independent of the final completion badge. */
export function RepairFlow({
  snapshot,
  select,
}: {
  snapshot: Snapshot;
  select: (value: Selection) => void;
}) {
  const flows = useMemo(() => buildRepairFlows(snapshot), [snapshot]);
  if (!flows.length) return null;
  return (
    <section className="repair-flows" aria-label="QA 실패와 수정·재검증 흐름">
      {flows.map((flow) => (
        <section
          className="repair-flow"
          key={flow.id}
          data-repair-flow={flow.id}
          aria-labelledby={`repair-title-${flow.task_id}`}
        >
          <header className="repair-flow-header">
            <div>
              <p className="repair-eyebrow">
                QA 수정 흐름 <span>{flow.run_id}</span>
              </p>
              <h2 id={`repair-title-${flow.task_id}`}>실패부터 재검증까지</h2>
            </div>
            <span
              className={`repair-outcome is-${flow.state}`}
              data-repair-state={flow.state}
            >
              {flowLabel(flow)}
            </span>
          </header>
          <div className="repair-flow-meta">
            <span>{flow.title}</span>
            <p>
              <strong>
                {flow.qa_attempts_count == null
                  ? "QA 횟수 미보고"
                  : `QA ${flow.qa_attempts_count}회`}
              </strong>
              <span>실패 {flow.failed_attempt_count}회</span>
              <span>연결된 수정 {flow.repair_attempt_count}회</span>
            </p>
          </div>
          <ol
            className="repair-stages"
            aria-label={`${flow.task_id} 시도별 실행 기록`}
          >
            {flow.stages.map((stage) => (
              <li
                className={`repair-stage is-${stageTone(stage)}`}
                key={stage.id}
                data-repair-kind={stage.kind}
                data-attempt={stage.attempt ?? ""}
                data-recorded-status={stage.status}
              >
                <div className="repair-stage-heading">
                  <h3>{stageLabel(stage)}</h3>
                  <span className="repair-stage-status">
                    {statusLabel(stage)}
                  </span>
                </div>
                <p className="repair-stage-caption">
                  {stage.kind === "failure"
                    ? "실패 원인 · 실행 기록"
                    : stage.kind === "repair"
                      ? "수정 내용 · 실행 기록"
                      : "재검증 결과 · 실행 기록"}
                </p>
                <p className="repair-stage-summary">
                  {stage.summary || "추가 설명이 보고되지 않았습니다."}
                </p>
                {stage.current_status !== stage.status && (
                  <p className="repair-stage-followup">
                    <strong>
                      후속 기록 ·{" "}
                      {(
                        {
                          blocked: "차단",
                          awaiting_decision: "판단 대기",
                          running: "진행 중",
                        } as Record<string, string>
                      )[stage.current_status] || stage.current_status}
                    </strong>
                    {stage.latest_summary}
                  </p>
                )}
                <footer>
                  <button
                    type="button"
                    onClick={() => select({ kind: "task", id: stage.task_id })}
                    aria-label={`${stage.task_id} ${stageLabel(stage)} 작업 보기`}
                  >
                    {stage.task_id}
                  </button>
                  <span>{ROLE[stage.role] || stage.role}</span>
                  <time
                    dateTime={stage.at || undefined}
                    title={stage.at || undefined}
                  >
                    {recordedTime(stage)}
                  </time>
                </footer>
              </li>
            ))}
          </ol>
          <div className="repair-flow-notes">
            {flow.repair_attempt_count === 0 && (
              <p>실패 근거와 연결된 개발 수정 기록이 아직 없습니다.</p>
            )}
            {flow.ambiguous_repairs > 0 && (
              <p>
                여러 실패와 연결될 수 있는 수정 기록 {flow.ambiguous_repairs}
                건은 순서를 확정하지 않고 제외했습니다.
              </p>
            )}
            {flow.ordering === "recorded" && (
              <p>시각·원장 순서가 없는 기록은 단계별 기록 순서로 표시합니다.</p>
            )}
            {flow.history_omitted > 0 && (
              <p>
                원본 기록 중 이전 {flow.history_omitted}건이 API 표시 범위
                밖입니다. 실패 횟수는 표시된 기록 기준입니다.
              </p>
            )}
            {flow.omitted_stages > 0 && (
              <p>
                전체 {flow.total_stages}개 단계 중 최근 {flow.stages.length}개를
                표시합니다.
              </p>
            )}
          </div>
        </section>
      ))}
    </section>
  );
}
