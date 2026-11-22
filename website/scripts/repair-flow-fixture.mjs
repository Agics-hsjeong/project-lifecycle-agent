/** Synthetic display fixtures only; no customer files or model calls. */
export const H1='a'.repeat(64), H2='b'.repeat(64), H3='c'.repeat(64);
export const evidence=()=>({'tests/qa.py':H1,'reports/initial.log':H2});
export const row=(task,role,attempt,status,sequence,summary,evidence_hashes={})=>({
 id:`RUN-CURRENT:${task}:${sequence??'unknown'}:${status}:${attempt??'legacy'}`,
 task_id:task,role,attempt,status,sequence,
 at:sequence==null?null:`2026-11-23T00:${String(sequence).padStart(2,'0')}:00Z`,
 summary,evidence_hashes,
});
export function fixture(){
 return {schema_version:1,revision:'r1',captured_at:'2026-11-23T01:00:00Z',
  project:{id:'synthetic-repair',name:'합성 수정 흐름',current_stage:'verification'},
  tasks:[
   {id:'TASK-QA',title:'QA 수용 검증',role:'qa',status:'done',run_id:'RUN-CURRENT',step_id:'STEP-QA',step_ref:{run_id:'RUN-CURRENT',step_id:'STEP-QA'},depends_on:[],requirement_ids:[],artefact_ids:[]},
   {id:'TASK-DEV',title:'개발 수정',role:'backend',status:'done',run_id:'RUN-CURRENT',step_id:'STEP-DEV',step_ref:{run_id:'RUN-CURRENT',step_id:'STEP-DEV'},depends_on:[],requirement_ids:[],artefact_ids:[]},
  ],artefacts:[],agents:[],gates:[],usage:{tokens:null,cost:null},
  current_scope:{id:'RUN-CURRENT',run_id:'RUN-CURRENT',title:'현재 수정',source:'latest_run',task_ids:['TASK-QA','TASK-DEV']},
  history_runs:[{id:'RUN-CURRENT',title:'현재 수정',scope_id:'RUN-CURRENT',steps:[
   {id:'STEP-QA',task_id:'TASK-QA',role:'qa',title:'QA 수용 검증',status:'succeeded',valid:true,attempts:2,inputs:[],expected_outputs:[],receipt_hashes:{},history_omitted:0,result_history:[
    row('TASK-QA','qa',1,'running',1,'최초 QA 실행'),
    row('TASK-QA','qa',1,'failed',2,'True 수량이 허용돼 입력 계약 위반',evidence()),
    row('TASK-QA','qa',2,'running',5,'같은 반례로 재검증',evidence()),
    row('TASK-QA','qa',2,'succeeded',6,'동일 반례 15개 통과',evidence()),
   ]},
   {id:'STEP-DEV',task_id:'TASK-DEV',role:'backend',title:'개발 수정',status:'succeeded',valid:true,attempts:1,inputs:[],expected_outputs:[],receipt_hashes:{},history_omitted:0,result_history:[
    row('TASK-DEV','backend',1,'running',3,'QA 실패 반례를 전달받음',evidence()),
    row('TASK-DEV','backend',1,'succeeded',4,'내장 int만 허용하도록 수정',{'src/quote.py':H3}),
   ]},
  ]}],
 };
}
