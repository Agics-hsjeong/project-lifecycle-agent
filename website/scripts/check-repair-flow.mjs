/** Synthetic regressions for the repair-flow projection. No actual task states are injected. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalise, taskSummary, verificationOf, hasIssue, waiting } from '../src/workspace/model.ts';
import { buildRepairFlows } from '../src/workspace/repair-flow.ts';

import { H1, H2, H3, row, fixture, multiRunFixture, evidence } from './repair-flow-fixture.mjs';

const qa=s=>s.history_runs[0].steps[0];
const dev=s=>s.history_runs[0].steps[1];
const flows=s=>buildRepairFlows(normalise(s));
const only=s=>{const f=flows(s);assert.equal(f.length,1);return f[0];};

test('failure, explicit repair and same-step retry stay visible after the latest success',()=>{
 const s=fixture(),f=only(s);
 assert.deepEqual(f.stages.map(x=>x.kind),['failure','repair','recheck']);
 assert.deepEqual(f.stages.map(x=>x.attempt),[1,1,2]);
 assert.equal(f.qa_attempts_count,2);assert.equal(f.failed_attempt_count,1);assert.equal(f.repair_attempt_count,1);assert.equal(f.state,'passed');
 assert.match(f.stages[0].summary,/True 수량/);assert.match(f.stages[1].summary,/내장 int/);assert.match(f.stages[2].summary,/15개 통과/);
 assert.equal(f.ordering,'chronological');assert.equal(f.history_omitted,0);
});

test('old snapshots without retry projection remain compatible and do not invent history',()=>{
 const s=fixture();for(const step of s.history_runs[0].steps){delete step.result_history;delete step.attempts;delete step.history_omitted;}
 assert.deepEqual(flows(s),[]);
});

test('an unrelated historical failed run does not enter the current scope',()=>{
 const s=fixture(),past=structuredClone(s.history_runs[0]);past.id='RUN-PAST';past.scope_id='RUN-PAST';for(const step of past.steps){step.task_id=null;step.role='unassigned';for(const record of step.result_history){record.id=record.id.replace('RUN-CURRENT','RUN-PAST');record.sequence+=100;record.task_id=null;record.role='unassigned';}}past.steps[0].result_history[1].summary='다른 실행의 실패';s.history_runs.unshift(past);
 const f=only(s);assert.equal(f.run_id,'RUN-CURRENT');assert(f.stages.every(x=>!x.summary.includes('다른 실행')));
});

test('a missing current run and a mismatched task binding never select the latest arbitrary history',()=>{
 const s=fixture();s.current_scope.run_id='RUN-UNKNOWN';assert.deepEqual(flows(s),[]);
 const t=fixture();t.tasks[0].step_id='STEP-OTHER';assert.throws(()=>normalise(t));
});

test('conflicting flat and nested task execution references are rejected in either direction',()=>{
 const first=fixture();first.tasks[0].step_ref={run_id:'RUN-OTHER',step_id:'STEP-OTHER'};assert.throws(()=>normalise(first));
 const second=fixture();second.tasks[0].step_ref={run_id:'RUN-CURRENT',step_id:'STEP-QA'};second.tasks[0].run_id='RUN-OTHER';assert.throws(()=>normalise(second));
});

test('matching flat and nested references keep the same current flow',()=>{
 const s=fixture();s.tasks[0].step_ref={run_id:'RUN-CURRENT',step_id:'STEP-QA'};s.tasks[1].step_ref={run_id:'RUN-CURRENT',step_id:'STEP-DEV'};
 assert.deepEqual(only(s).stages.map(stage=>stage.kind),['failure','repair','recheck']);
});

test('tasks excluded from the current scope cannot be attached as repair workers',()=>{
 const s=fixture();s.current_scope.task_ids=['TASK-QA'];const f=only(s);assert.equal(f.repair_attempt_count,0);assert(!f.stages.some(x=>x.kind==='repair'));
});

test('sharing a path with a different digest does not establish a repair relationship',()=>{
 const s=fixture();dev(s).result_history[0].evidence_hashes={'tests/qa.py':H3,'reports/initial.log':H3};
 const f=only(s);assert.equal(f.repair_attempt_count,0);assert(!f.stages.some(x=>x.kind==='repair'));
});

test('a protected regression code file alone establishes an explicit hash-matched handoff',()=>{
 const s=fixture();qa(s).result_history[1].evidence_hashes={'tests/protected_regression.py':H1};dev(s).result_history[0].evidence_hashes={'tests/protected_regression.py':H1};
 assert.equal(only(s).repair_attempt_count,1);
});

test('unknown cross-step time and sequence stay explicitly recorded rather than fabricated',()=>{
 const s=fixture();for(const step of s.history_runs[0].steps)for(const r of step.result_history){r.at=null;r.sequence=null;}
 const f=only(s);assert.equal(f.ordering,'recorded');assert(f.stages.every(x=>x.at==null&&x.sequence==null));
});

test('a bounded visible timeline discloses earlier stages without erasing failure counts',()=>{
 const s=fixture();qa(s).attempts=14;qa(s).result_history=[];dev(s).result_history=[];
 for(let n=1;n<=14;n++){
  qa(s).result_history.push(row('TASK-QA','qa',n,'running',n*2-1,`QA ${n} 시작`));
  qa(s).result_history.push(row('TASK-QA','qa',n,n===14?'succeeded':'failed',n*2,n===14?'최종 통과':`실패 ${n}`,evidence()));
 }
 const f=only(s);assert.equal(f.qa_attempts_count,14);assert.equal(f.failed_attempt_count,13);assert.equal(f.total_stages,14);assert.equal(f.stages.length,12);assert.equal(f.omitted_stages,2);
});

test('mere chronological adjacency and an unrelated developer result do not establish a repair',()=>{
 const s=fixture();dev(s).result_history[0].evidence_hashes={'reports/unrelated.log':H2};
 const f=only(s);assert.equal(f.repair_attempt_count,0);assert(!f.stages.some(x=>x.kind==='repair'));
});

test('a document role consuming the same report is not presented as development repair',()=>{
 const s=fixture();s.tasks[1].role='technical-writer';dev(s).role='technical-writer';for(const r of dev(s).result_history)r.role='technical-writer';
 assert.equal(only(s).repair_attempt_count,0);
});

test('running repair and running retry are not promoted to verified completion',()=>{
 const s=fixture();qa(s).result_history=qa(s).result_history.slice(0,2);qa(s).status='failed';qa(s).attempts=1;s.tasks[0].status='failed';dev(s).result_history=dev(s).result_history.slice(0,1);dev(s).status='running';s.tasks[1].status='running';
 const f=only(s);assert.notEqual(f.state,'passed');assert.equal(f.stages.find(x=>x.kind==='repair').status,'running');
 const t=fixture();qa(t).result_history.pop();qa(t).status='running';t.tasks[0].status='running';assert.equal(only(t).state,'running');
});

test('a linked current repair still running prevents a green completed flow from an older QA success',()=>{
 const s=fixture();dev(s).result_history=dev(s).result_history.slice(0,1);dev(s).status='running';dev(s).valid=false;s.tasks[1].status='running';
 const f=only(s);assert.equal(f.state,'running');assert(f.stages.some(x=>x.kind==='repair'&&x.current_status==='running'&&x.is_latest));
});

test('a later blocked result does not erase the failure that prompted it',()=>{
 const s=fixture();qa(s).result_history=qa(s).result_history.slice(0,2);qa(s).result_history.push(row('TASK-QA','qa',1,'blocked',3,'같은 원인 반복으로 확인 필요'));qa(s).status='blocked';qa(s).attempts=1;s.tasks[0].status='blocked';dev(s).result_history=[];
 const f=only(s);assert.equal(f.failed_attempt_count,1);assert.equal(f.stages[0].kind,'failure');assert.match(f.stages[0].summary,/True 수량/);assert.equal(f.stages[0].current_status,'blocked');assert.equal(f.state,'unresolved');
});

test('a successful receipt with changed current evidence remains stale',()=>{
 const s=fixture();qa(s).valid=false;s.tasks[0].status='stale';const f=only(s);assert.equal(f.state,'stale');assert.notEqual(f.state,'passed');assert.equal(f.stages.at(-1).valid,false);
});

test('a legacy failed result without a recorded start keeps its attempt number unknown',()=>{
 const s=fixture();qa(s).attempts=null;qa(s).status='failed';qa(s).result_history=[row('TASK-QA','qa',null,'failed',null,'구형 실패 기록',evidence())];dev(s).result_history=[];s.tasks[0].status='failed';
 const f=only(s);assert.equal(f.stages[0].attempt,null);assert.equal(f.stages[0].started_at,null);assert.equal(f.stages[0].at,null);
});

test('known source sequence orders simultaneous timestamps without step-order fabrication',()=>{
 const s=fixture();for(const step of s.history_runs[0].steps)for(const r of step.result_history)r.at='2026-11-23T00:00:00Z';
 const f=only(s);assert.deepEqual(f.stages.map(x=>x.kind),['failure','repair','recheck']);assert.equal(f.repair_attempt_count,1);
});

test('missing timestamps never become capture time or the current wall clock',()=>{
 const s=fixture();for(const step of s.history_runs[0].steps)for(const r of step.result_history)r.at=null;
 const f=only(s);assert(f.stages.every(x=>x.at==null&&x.started_at==null&&x.finished_at==null));
 assert.deepEqual(f.stages.map(x=>x.kind),['failure','repair','recheck']);
});

test('ambiguous evidence without a cross-step chronology is not assigned to an arbitrary failure',()=>{
 const s=fixture();qa(s).attempts=3;qa(s).result_history=[row('TASK-QA','qa',1,'running',1,'1차'),row('TASK-QA','qa',1,'failed',2,'첫 실패',evidence()),row('TASK-QA','qa',2,'running',3,'2차'),row('TASK-QA','qa',2,'failed',4,'두 번째 실패',evidence()),row('TASK-QA','qa',3,'running',7,'3차'),row('TASK-QA','qa',3,'succeeded',8,'최종 통과')];
 for(const step of s.history_runs[0].steps)for(const r of step.result_history){r.at=null;r.sequence=null;}
 const f=only(s);assert.equal(f.failed_attempt_count,2);assert.equal(f.repair_attempt_count,0);assert.equal(f.ambiguous_repairs,1);
});

test('source truncation is disclosed rather than silently claiming a complete history',()=>{
 const s=fixture();qa(s).history_omitted=7;assert.equal(only(s).history_omitted,7);
});

test('the projection does not mutate current or historical source records',()=>{
 const s=fixture(),before=structuredClone(s);flows(s);assert.deepEqual(s,before);
});

const malformed=[
 ['attempt object',s=>qa(s).attempts={value:2}],
 ['negative attempt count',s=>qa(s).attempts=-1],
 ['fractional attempt',s=>qa(s).result_history[0].attempt=1.5],
 ['summary object',s=>qa(s).result_history[0].summary={html:'<script>'}],
 ['prototype role',s=>qa(s).result_history[0].role='constructor'],
 ['unknown status',s=>qa(s).result_history[0].status='approved'],
 ['invalid timestamp',s=>qa(s).result_history[0].at='not-a-time'],
 ['negative sequence',s=>qa(s).result_history[0].sequence=-1],
 ['hash object',s=>qa(s).result_history[1].evidence_hashes={'reports/qa.log':{sha:H1}}],
 ['invalid digest',s=>qa(s).result_history[1].evidence_hashes={'reports/qa.log':'wrong'}],
 ['negative omitted count',s=>qa(s).history_omitted=-1],
 ['result history object',s=>qa(s).result_history={results:[]}],
];
for(const[label,change]of malformed)test(`normalise rejects malformed repair history: ${label}`,()=>{
 const s=fixture();change(s);assert.throws(()=>normalise(s));
});

test("an explicit task scope spans exact QA and engineering run bindings", () => {
  const f = only(multiRunFixture());
  assert.deepEqual(
    f.stages.map((s) => s.kind),
    ["failure", "repair", "recheck"],
  );
  assert.deepEqual(
    f.stages.map((s) => s.run_id),
    ["RUN-CURRENT", "RUN-DEVELOPMENT", "RUN-CURRENT"],
  );
  assert.deepEqual(f.run_ids, ["RUN-CURRENT", "RUN-DEVELOPMENT"]);
  assert.equal(f.state, "passed");
});

test("an explicit run scope never widens itself to a second engineering run", () => {
  const s = multiRunFixture();
  s.current_scope.run_id = "RUN-CURRENT";
  assert.equal(only(s).repair_attempt_count, 0);
});

test("a rebound current task cannot consume its superseded historical execution", () => {
  const s = multiRunFixture();
  s.tasks[0].run_id = "RUN-NEW";
  s.tasks[0].step_ref.run_id = "RUN-NEW";
  s.tasks[0].generation = 2;
  assert.deepEqual(flows(s), []);
  const t = multiRunFixture();
  t.history_runs[1].steps[0].superseded = true;
  assert.equal(only(t).repair_attempt_count, 0);
});

test("an out-of-scope or stale-generation repair cannot join a multi-run QA flow", () => {
  const s = multiRunFixture();
  s.current_scope.task_ids = ["TASK-QA"];
  assert.equal(only(s).repair_attempt_count, 0);
  const t = multiRunFixture();
  t.tasks[1].run_id = "RUN-OLD";
  t.tasks[1].step_ref.run_id = "RUN-OLD";
  t.tasks[1].generation = 3;
  assert.equal(only(t).repair_attempt_count, 0);
});

test("duplicate historical bindings cannot choose an arbitrary current task generation", () => {
  const s = multiRunFixture();
  s.history_runs.push(structuredClone(s.history_runs[0]));
  // Direct model guard also applies before normalise can reject duplicate source rows.
  assert.deepEqual(buildRepairFlows(s), []);
});

test("multi-run repair needs exact evidence and an actual ordered handoff", () => {
  const s = multiRunFixture();
  s.history_runs[1].steps[0].result_history[0].evidence_hashes = {
    "tests/qa.py": H3,
  };
  assert.equal(only(s).repair_attempt_count, 0);
  const t = multiRunFixture();
  for (const r of t.history_runs)
    for (const st of r.steps)
      for (const row of st.result_history) {
        row.at = null;
        row.sequence = null;
      }
  const unknown = only(t);
  assert.equal(unknown.repair_attempt_count, 0);
  assert.equal(unknown.ambiguous_repairs, 1);
  const u = multiRunFixture();
  const start = u.history_runs[1].steps[0].result_history[0];
  start.sequence = 8;
  start.at = "2026-11-23T00:08:00Z";
  assert.equal(only(u).repair_attempt_count, 0);
});

test("multiple matching QA failures remain ambiguous across runs rather than cross-wired", () => {
  const s = multiRunFixture(),
    other = structuredClone(s.history_runs[0]);
  other.id = "RUN-OTHER-QA";
  other.steps[0].task_id = "TASK-OTHER-QA";
  const task = structuredClone(s.tasks[0]);
  task.id = "TASK-OTHER-QA";
  task.run_id = other.id;
  task.step_ref.run_id = other.id;
  for (const row of other.steps[0].result_history) {
    row.id = row.id.replace("TASK-QA", "TASK-OTHER-QA");
    row.task_id = task.id;
    row.sequence = null;
  }
  for (const run of s.history_runs)
    for (const st of run.steps)
      for (const row of st.result_history) row.sequence = null;
  s.tasks.push(task);
  s.history_runs.push(other);
  s.current_scope.task_ids.push(task.id);
  const f = flows(s);
  assert.equal(f.length, 2);
  assert(
    f.every((x) => x.repair_attempt_count === 0 && x.ambiguous_repairs === 1),
  );
});

test("temporarily unreadable verification preserves completion receipts without declaring stale or passed", () => {
  const s = multiRunFixture({ unavailable: true }),
    f = only(s);
  assert.equal(f.state, "unavailable");
  assert.deepEqual(
    f.stages.map((x) => x.kind),
    ["failure", "repair", "recheck"],
  );
  assert.match(f.stages.at(-1).summary, /15개 통과/);
  assert.equal(f.stages.at(-1).verification_state, "unavailable");
  const n = normalise(s),
    counts = taskSummary(n.tasks, n);
  assert.equal(counts.done, 0);
  assert.equal(counts.percent, 0);
  assert.equal(counts.recorded_done, 2);
  assert.equal(counts.recorded_percent, 100);
  assert.equal(counts.verification_unavailable, 2);
  assert.equal(counts.waiting, 0);
  assert.equal(counts.attention, 2);
  assert.equal(verificationOf(n.tasks[0]).state, "unavailable");
  assert(hasIssue(n.tasks[0], n));
  assert(!waiting(n.tasks[0], n));
});

test("restored evidence returns current completion while genuinely changed evidence remains stale", () => {
  const s = multiRunFixture({ unavailable: true });
  for (const task of s.tasks) {
    task.status = "done";
    task.verification_state = "valid";
  }
  for (const run of s.history_runs)
    for (const step of run.steps) {
      step.valid = true;
      step.verification_state = "valid";
    }
  assert.equal(only(s).state, "passed");
  assert.equal(taskSummary(normalise(s).tasks).percent, 100);
  s.tasks[0].status = "stale";
  s.tasks[0].verification_state = "changed";
  s.history_runs[0].steps[0].verification_state = "changed";
  s.history_runs[0].steps[0].valid = false;
  assert.equal(only(s).state, "stale");
  assert.equal(taskSummary(normalise(s).tasks).done, 1);
});

for (const [label, change] of [
  [
    "task current verification object",
    (s) => (s.tasks[0].verification_state = {}),
  ],
  [
    "task unknown current verification state",
    (s) => (s.tasks[0].verification_state = "approved"),
  ],
  ["task recorded status object", (s) => (s.tasks[0].recorded_status = {})],
  ["step verification object", (s) => (qa(s).verification_state = {})],
  [
    "step unknown verification state",
    (s) => (qa(s).verification_state = "temporarily-ok"),
  ],
])
  test(`normalise rejects malformed verification projection: ${label}`, () => {
    const s = fixture();
    change(s);
    assert.throws(() => normalise(s));
  });
