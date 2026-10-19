import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

class Node {
  constructor() { this.children=[]; this.listeners={}; this.value=""; this.checked=false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  addEventListener(type, fn) { this.listeners[type]=fn; }
  fire(type="click") { return this.listeners[type]?.({preventDefault() {}}); }
}
globalThis.document={createElement: tag => Object.assign(new Node(), {tagName:tag.toUpperCase()})};
const text=n => [n.textContent || "", ...n.children.map(text)].join(" ");
const buttons=n => [...(n.listeners.click ? [n] : []), ...n.children.flatMap(buttons)];
const deferred=() => { let resolve,reject; const promise=new Promise((y,n) => {resolve=y;reject=n;}); return {promise,resolve,reject}; };
const file={id:"f",current_version_id:"v",name:"<img onerror=bad>",remote_ai_policy:"remote_ai_allowed",deleted:false};
const version={id:"v",remote_ai_approved:true,version_number:1};
const task={id:"t",project_id:"p",state:"pending",versions:[{file_id:"f",version_id:"v"}],created_at:"2026-10-18",updated_at:"2026-10-18",confirmed_at:"2026-10-18"};
const result={state:"completed",model_calls:2,error_code:null,result:{answer:"<script>answer</script>",citations:[{file_id:"f",version_id:"v",chunk_id:"c"}]}};
const audit={kind:"backend_requests",capacity:64,items:[{task_id:"t",sequence:1,request_id:"a".repeat(32),
  tool:"search_documents",parameters:{mode:"keyword",query_bytes:6},status:"success",started_at:"2026-10-19 00:00:00.000000",duration_ms:0}]};
async function setup(overrides={}) {
  const url=new URL("../../resources/js/agent.js",import.meta.url);
  assert.ok(existsSync(url),"Agent task UI not implemented");
  const {AgentPanel}=await import(url.href);
  const nodes=new Map(), calls=[];
  const get=id => {if(!nodes.has(id)) nodes.set(id,new Node()); return nodes.get(id);};
  const api={listAgentTasks:async (...a) => {calls.push(["list",...a]); return {items:[task],has_more:true,next_before:"t",page_size:20};},
    createAgentTask:async (...a) => {calls.push(["create",...a]); return task;},
    getAgentTask:async (...a) => {calls.push(["get",...a]); return task;},
    cancelAgentTask:async (...a) => {calls.push(["cancel",...a]); return {...task,state:"cancelled"};},
    getAgentResult:async (...a) => {calls.push(["result",...a]); return result;},
    getAgentToolCalls:async (...a) => {calls.push(["audit",...a]); return audit;},
    readDocument:async (...a) => {calls.push(["source",...a]); return {file_id:"f",version_id:"v",is_current:false,chunk:{chunk_id:"c",text:"<img>source",page:2,line_start:1,line_end:3}};},
    versionContentUrl:(p,f,v)=>`/api/${p}/${f}/${v}/content`,...overrides};
  const panel=new AgentPanel(get,api,e=>e.message);
  panel.setProject("p");
  return {panel,get,calls};
}
function draft(view) {
  view.panel.addVersion("p",file,version);
  view.get("agent-question").value="Check public sample";
  view.get("agent-question").fire("input");
  view.get("agent-consent").checked=true;
}

const executeButton = v => buttons(v.get("agent-detail")).find(b=>b.textContent==="Start this task");
const executeConsent = v => descendants(v.get("agent-detail")).find(n=>n.type==="checkbox");
async function approveExecution(v) {const box=executeConsent(v);assert.ok(box);box.checked=true;await box.fire("change");}

test("execution requires a fresh explicit pending-task decision and reads the authoritative result",async()=>{
  let state="pending",runs=0;const view=await setup({getAgentTask:async()=>({...task,state}),
    executeAgentTask:async(p,id)=>{assert.deepEqual([p,id],["p","t"]);runs++;state="completed";return {state:"failed"};}});
  await view.panel.view("t");const start=executeButton(view);assert.ok(start);assert.equal(start.disabled,true);
  await start.fire();assert.equal(runs,0);await approveExecution(view);await start.fire();
  assert.equal(runs,1);assert.match(text(view.get("agent-detail")),/<script>answer/);
  assert.equal(executeButton(view),undefined);assert.equal(view.calls.filter(c=>c[0]==="result").length,1);
  await start.fire();assert.equal(runs,1);
});

test("execution never appears on nonpending tasks or reader archive tasks",async()=>{
  for(const state of ["running","failed","completed","cancelled","interrupted","waiting_confirmation","waiting_execution"]){
    const view=await setup({getAgentTask:async()=>({...task,state}),getAgentResult:async()=>({...result,state,result:null})});
    await view.panel.view("t");assert.equal(executeButton(view),undefined);
  }
  const view=await setup({getAgentTask:async()=>({...task,archive_policy_revision:1})});
  await view.panel.view("t");assert.equal(executeButton(view),undefined);
});

test("in-flight execution keeps refresh and cancellation usable without allowing duplicate starts",async()=>{
  const late=deferred();let runs=0,state="pending";
  const view=await setup({executeAgentTask:()=>{runs++;state="running";return late.promise;},
    getAgentTask:async()=>({...task,state}),cancelAgentTask:async()=>{state="cancelled";}});
  await view.panel.view("t");await approveExecution(view);const start=executeButton(view),pending=start.fire();
  await start.fire();assert.equal(runs,1);assert.equal(view.get("agent-refresh").disabled,false);
  await buttons(view.get("agent-detail")).find(b=>b.textContent==="Refresh task").fire();
  assert.match(view.get("agent-detail-status").textContent,/running/);
  await buttons(view.get("agent-detail")).find(b=>b.textContent==="Cancel task").fire();
  assert.match(view.get("agent-detail-status").textContent,/cancelled/);
  late.resolve({state:"completed"});await pending;
  assert.match(view.get("agent-detail-status").textContent,/cancelled/);assert.equal(runs,1);
});

test("late execution responses cannot refill switched views or release the in-flight lock early",async()=>{
  for(const settle of ["resolve","reject"]) {
    const late=deferred();let runs=0;
    const view=await setup({executeAgentTask:()=>{runs++;return late.promise;}});
    await view.panel.view("t");await approveExecution(view);const pending=executeButton(view).fire();
    view.panel.setProject("other");await view.panel.view("t");
    const start=executeButton(view);await approveExecution(view);await start.fire();assert.equal(runs,1);
    view.panel.setProject(null);late[settle](settle==="resolve"?{state:"completed"}:new Error("old secret"));await pending;
    assert.equal(view.get("agent-detail").children.length,0);
    assert.doesNotMatch(view.get("agent-detail-status").textContent,/old secret|completed/);
  }
});

test("lost execution response requires read-only refresh and never retries the write",async()=>{
  let runs=0,state="pending";const view=await setup({getAgentTask:async()=>({...task,state}),
    executeAgentTask:async()=>{runs++;state="completed";throw new Error("connection lost");}});
  await view.panel.view("t");await approveExecution(view);await executeButton(view).fire();
  assert.match(view.get("agent-detail-status").textContent,/unknown.*Refresh.*no automatic retry/i);
  assert.equal(executeButton(view),undefined);assert.equal(view.calls.filter(c=>c[0]==="result").length,0);
  await buttons(view.get("agent-detail")).find(b=>b.textContent==="Refresh task").fire();
  assert.match(text(view.get("agent-detail")),/<script>answer/);assert.equal(runs,1);
});

test("execution consent resets on refresh and stale controls cannot start a task",async()=>{
  let runs=0;const view=await setup({executeAgentTask:async()=>{runs++;return {state:"unknown"};}});
  await view.panel.view("t");await approveExecution(view);const stale=executeButton(view);
  await view.panel.view("t");assert.equal(executeConsent(view).checked,false);
  await stale.fire();await executeButton(view).fire();assert.equal(runs,0);
});

test("execution adapter sends only empty JSON to the same origin without redirects or retries",async()=>{
  const api=await import(new URL("../../resources/js/api.js",import.meta.url));
  assert.equal(typeof api.executeAgentTask,"function");const original=globalThis.fetch,calls=[];
  globalThis.fetch=async(...args)=>{calls.push(args);return new Response(JSON.stringify({error:{code:"agent_busy",message:"Busy",retryable:true}}),{status:503});};
  try {
    await assert.rejects(api.executeAgentTask("p/x","t/y"),/Busy/);assert.equal(calls.length,1);
    const [url,options]=calls[0];assert.equal(url,"/api/v1/projects/p%2Fx/agent-tasks/t%2Fy/execute");
    assert.equal(options.method,"POST");assert.equal(options.body,"{}");assert.equal(options.credentials,"same-origin");
    assert.equal(options.redirect,"error");assert.equal(options.cache,"no-store");assert.ok(options.signal);
  } finally {globalThis.fetch=original;}
});

test("execution adapter aborts at its deadline, clears its timer and does not resend",async()=>{
  const api=await import(new URL("../../resources/js/api.js",import.meta.url));
  const original={fetch:globalThis.fetch,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout};
  let expire,cleared=0,requests=0;const timer={};
  globalThis.setTimeout=(fn,ms)=>{assert.ok(ms>0&&ms<=300000);expire=fn;return timer;};
  globalThis.clearTimeout=value=>{assert.equal(value,timer);cleared++;};
  globalThis.fetch=async(url,options)=>{requests++;return new Promise((resolve,reject)=>
    options.signal.addEventListener("abort",()=>reject(new Error("deadline exceeded")),{once:true}));};
  try {const pending=api.executeAgentTask("p","t");expire();await assert.rejects(pending,/deadline/);
    assert.equal(requests,1);assert.equal(cleared,1);
  } finally {Object.assign(globalThis,original);}
});

const checklist={revision:7,remote_ai_approved:true,items:[{key:"deploy",title:"<script>Deployment</script>",description:"Check rollback",required:true}]};
const policy={revision:9,remote_ai_approved:true,targets:[{directory_id:"destination",name:"<img>Archive"}]};
async function mode(view, value) {
  view.get("agent-mode").value=value;
  await view.get("agent-mode").fire("change");
}

test("task modes bind only the reviewed server revision and never send metadata bodies",async()=>{
  for(const [choice,role,field,revision] of [["question","reader",null,null],
    ["handover","reader","handover_checklist_revision",7],["archive","editor","archive_policy_revision",9]]) {
    const reads=[];const view=await setup({getHandoverChecklist:async p=>{reads.push(p);return checklist;},
      getArchivePolicy:async p=>{reads.push(p);return policy;}});
    view.panel.setProject("p",role);draft(view);await mode(view,choice);
    assert.equal(view.get("agent-consent").checked,false);
    if(field) {
      view.get("agent-consent").checked=true;await view.panel.create();assert.equal(view.calls.length,0);
      await view.get("agent-context-refresh").fire();assert.deepEqual(reads,["p"]);
      assert.equal(view.get("agent-consent").checked,false);
      assert.match(text(view.get("agent-context")),choice==="handover"?/Check rollback/:/<img>Archive/);
      assert.equal(descendants(view.get("agent-context")).filter(n=>["SCRIPT","IMG"].includes(n.tagName)).length,0);
    }
    view.get("agent-consent").checked=true;await view.panel.create();
    const expected={question:"Check public sample",versions:[{file_id:"f",version_id:"v"}],confirm_remote_ai:true};
    if(field)expected[field]=revision;
    assert.deepEqual(view.calls.filter(c=>c[0]==="create"),[["create","p",expected]]);
    if(!field)assert.deepEqual(reads,[]);
  }
});

test("missing, unapproved, malformed or denied contexts cannot silently become question tasks",async()=>{
  for(const value of [{...checklist,revision:0},{...checklist,revision:true},{...checklist,revision:4294967296},
    {...checklist,remote_ai_approved:false},{...checklist,items:[]},null,new Error("forbidden")]) {
    const view=await setup({getHandoverChecklist:async()=>{if(value instanceof Error)throw value;return value;}});
    draft(view);await mode(view,"handover");await view.get("agent-context-refresh").fire();
    view.get("agent-consent").checked=true;await view.panel.create();
    assert.equal(view.calls.length,0);assert.equal(view.get("agent-context").children.length,0);
    assert.equal(view.get("agent-create").disabled,true);
  }
  const view=await setup({getArchivePolicy:()=>assert.fail("reader must not load archive task context")});
  draft(view);await mode(view,"archive");await view.get("agent-context-refresh").fire();
  view.get("agent-consent").checked=true;await view.panel.create();assert.equal(view.calls.length,0);
  assert.equal(view.get("agent-mode-archive").disabled,true);
  await mode(view,"unknown");await view.panel.create();assert.equal(view.calls.length,0);
});

test("context refresh revokes consent, clears old metadata and ignores obsolete responses",async()=>{
  for(const change of [v=>mode(v,"question"),v=>v.panel.setProject(null),v=>v.panel.setProject("other"),
    v=>v.panel.setProject("p","editor"),v=>v.panel.newDraft(),v=>v.panel.invalidate()]) {
    const late=deferred();let reads=0;
    const view=await setup({getHandoverChecklist:()=>{reads++;return reads===1?Promise.resolve(checklist):late.promise;}});
    draft(view);await mode(view,"handover");await view.get("agent-context-refresh").fire();
    view.get("agent-consent").checked=true;const pending=view.get("agent-context-refresh").fire();
    assert.equal(reads,2);assert.equal(view.get("agent-consent").checked,false);
    assert.equal(view.get("agent-context").children.length,0);
    await change(view);late.resolve(checklist);await pending;
    assert.equal(view.get("agent-context").children.length,0);
  }
  const late=deferred();let reads=0;
  const view=await setup({getHandoverChecklist:()=>++reads===1?late.promise:Promise.resolve({...checklist,revision:8})});
  draft(view);await mode(view,"handover");const old=view.get("agent-context-refresh").fire();
  await view.get("agent-context-refresh").fire();late.resolve(checklist);await old;
  view.get("agent-consent").checked=true;await view.panel.create();
  assert.equal(view.calls.find(c=>c[0]==="create")[2].handover_checklist_revision,8);
});

test("mode changes cannot release a creation lock or bypass unknown creation outcome",async()=>{
  const late=deferred();let creates=0;
  const view=await setup({getHandoverChecklist:async()=>checklist,createAgentTask:()=>{creates++;return late.promise;}});
  draft(view);await mode(view,"handover");await view.get("agent-context-refresh").fire();
  view.get("agent-consent").checked=true;const pending=view.panel.create();
  assert.equal(view.get("agent-mode").disabled,true);await mode(view,"question");await view.panel.create();
  assert.equal(creates,1);late.reject(new Error("lost"));await pending;
  await mode(view,"question");draft(view);await view.panel.create();assert.equal(creates,1);
  assert.equal(view.get("agent-create").disabled,true);
});

test("task context GET adapters encode project identity and use same-origin credentials",async()=>{
  const api=await import(new URL("../../resources/js/api.js",import.meta.url));
  assert.equal(typeof api.getHandoverChecklist,"function");assert.equal(typeof api.getArchivePolicy,"function");
  const original=globalThis.fetch,calls=[];
  globalThis.fetch=async(...args)=>{calls.push(args);return new Response(JSON.stringify({data:checklist}));};
  try {
    await api.getHandoverChecklist("p/x");await api.getArchivePolicy("p/x");
    assert.deepEqual(calls.map(c=>c[0]),["/api/v1/projects/p%2Fx/handover-checklist","/api/v1/projects/p%2Fx/archive-policy"]);
    for(const [,options] of calls) {assert.equal(options.method,"GET");assert.equal(options.credentials,"same-origin");assert.equal(options.body,undefined);}
  } finally {globalThis.fetch=original;}
});

const reportRecord={id:"r",version:1,project_id:"p",task_id:"t",state:"draft",
  verification:"citations_validated_not_semantically_reviewed",created_at:"2026-10-19",
  checklist:{revision:2,items:[{key:"a",title:"Deployment",required:true},{key:"b",title:"Tests",required:true},{key:"c",title:"Notes",required:false}]},
  selected_versions:[{file_id:"f",version_id:"v"},{file_id:"unseen",version_id:"u"}],
  source_versions:[{file_id:"f",version_id:"v"},{file_id:"uncited",version_id:"x"}],
  url:"javascript:bad",markdown_url:"https://untrusted.invalid/report",
  report:{summary:"<script>report</script>",limitations:"Only searched selected scope",recommendations:"Manual review required",
    items:[{key:"a",title:"Deployment",required:true,status:"found",notes:"Not accepted",citations:[{
      file_id:"f",version_id:"v",chunk_id:"c",file_name:"<img onerror=bad>",page:2,line_start:1,line_end:3,
      text:"Evidence snippet",source_url:"javascript:evil"}]},
    {key:"b",title:"Tests",required:true,status:"missing",notes:"No evidence within scope",citations:[]},
    {key:"c",title:"Notes",required:false,status:"uncertain",notes:"Needs checking",citations:[]}]}};
async function reportSetup(overrides={}) {
  return setup({getAgentTask:async()=>({...task,state:"completed"}),
    getAgentResult:async()=>({state:"completed",kind:"handover_report",result:null,error_code:null,model_calls:3,
      handover_report:{id:"r",version:1,url:"javascript:bad",markdown_url:"https://untrusted.invalid/report"}}),
    getHandoverReport:async()=>structuredClone(reportRecord),...overrides});
}

test("report displays private draft, all statuses, distinct scope and untrusted content as text",async()=>{
  const view=await reportSetup();await view.panel.view("t");
  const content=text(view.get("agent-detail"));
  assert.match(text(view.get("agent-detail-status")),/handover report/i);
  for(const marker of ["<script>report</script>","Evidence snippet","Only searched selected scope","Manual review required",
    "Deployment","Required","Optional","Found","Missing within searched scope","Uncertain","unseen","uncited","revision 2","not semantically reviewed"])
    assert.ok(content.includes(marker),marker);
  assert.equal(descendants(view.get("agent-detail")).filter(n=>["SCRIPT","IMG"].includes(n.tagName)).length,0);
  assert.equal(descendants(view.get("agent-detail")).filter(n=>n.href).length,0,"no unchecked model URLs");
  assert.ok(buttons(view.get("agent-detail")).some(b=>b.textContent==="Download report Markdown"));
});

test("report citation rechecks whole report dependencies and clears all old content on denial",async()=>{
  let checks=0;
  const view=await reportSetup({getHandoverReport:async()=>{checks++;if(checks>2)throw new Error("task_scope_changed");return reportRecord;}});
  await view.panel.view("t");
  const source=buttons(view.get("agent-detail")).find(b=>b.textContent==="Read cited source");assert.ok(source);
  await source.fire();assert.equal(checks,2);
  assert.deepEqual(view.calls.filter(c=>c[0]==="source"),[["source","p","f","v","c"]]);
  assert.equal(view.get("agent-source").children.at(-1).href,"/api/p/f/v/content#page=2");
  await source.fire();await source.fire();assert.equal(checks,3);
  for(const part of ["detail","source","audit"])assert.equal(view.get("agent-"+part).children.length,0);
});

test("report stale loads cannot survive logout, project, role or task changes",async()=>{
  for(const change of [p=>p.setProject(null),p=>p.setProject("other"),p=>p.setProject("p","editor"),p=>p.clearDetail()]) {
    let reads=0;const late=deferred();const view=await reportSetup({getHandoverReport:()=>{reads++;return late.promise;}});
    const pending=view.panel.view("t");await Promise.resolve();await Promise.resolve();assert.equal(reads,1);change(view.panel);
    late.resolve(reportRecord);await pending;
    assert.doesNotMatch(text(view.get("agent-detail")),/Evidence snippet|<script>report/);
  }
});

test("report identity mismatch and removed citation never expose mismatched content",async()=>{
  for(const patch of [{id:"other"},{project_id:"other"},{task_id:"other"},{version:2},{state:"published"}]) {
    const view=await reportSetup({getHandoverReport:async()=>({...reportRecord,...patch})});await view.panel.view("t");
    assert.equal(view.get("agent-detail").children.length,0);
  }
  const view=await reportSetup();await view.panel.view("t");
  const source=buttons(view.get("agent-detail")).find(b=>b.textContent==="Read cited source");
  view.panel.api.getHandoverReport=async()=>({...reportRecord,report:{...reportRecord.report,items:[]}});
  await source.fire();assert.equal(view.calls.filter(c=>c[0]==="source").length,0);
  assert.equal(view.get("agent-detail").children.length,0);
});

test("report download uses fresh authenticated blob, discards late data and clears denial",async()=>{
  const downloads=[],revoked=[];const create=URL.createObjectURL,revoke=URL.revokeObjectURL,click=Node.prototype.click;
  URL.createObjectURL=blob=>{assert.ok(blob instanceof Blob);return "blob:fixture";};
  URL.revokeObjectURL=url=>revoked.push(url);Node.prototype.click=function(){downloads.push([this.href,this.download]);};
  try {
    let reads=0;const view=await reportSetup({downloadHandoverReport:async(p,t)=>{assert.deepEqual([p,t],["p","t"]);reads++;return new Blob(["safe markdown"]);}});
    await view.panel.view("t");await clickNamed(view,"agent-detail","Download report Markdown");
    assert.equal(reads,1);assert.deepEqual(downloads,[["blob:fixture","handover-report.md"]]);assert.deepEqual(revoked,["blob:fixture"]);
    const late=deferred();view.panel.api.downloadHandoverReport=()=>late.promise;
    const pending=clickNamed(view,"agent-detail","Download report Markdown");view.panel.setProject(null);
    late.resolve(new Blob(["private"]));await pending;assert.equal(downloads.length,1);
    view.panel.setProject("p");await view.panel.view("t");
    view.panel.api.downloadHandoverReport=async()=>{throw new Error("task_scope_changed");};
    await clickNamed(view,"agent-detail","Download report Markdown");
    assert.equal(view.get("agent-detail").children.length,0);assert.equal(downloads.length,1);
  } finally {URL.createObjectURL=create;URL.revokeObjectURL=revoke;Node.prototype.click=click;}
});

test("report HTTP adapters bind encoded task identity, avoid caches and reject failed downloads",async()=>{
  const api=await import(new URL("../../resources/js/api.js",import.meta.url));
  assert.equal(typeof api.getHandoverReport,"function");assert.equal(typeof api.downloadHandoverReport,"function");
  const original=globalThis.fetch,calls=[];
  globalThis.fetch=async(...args)=>{calls.push(args);return args[0].endsWith("/markdown")?
    new Response("# Safe",{headers:{"Content-Type":"text/markdown; charset=utf-8"}}):new Response(JSON.stringify({data:reportRecord}));};
  try {
    assert.deepEqual(await api.getHandoverReport("p/x","t?x"),reportRecord);
    assert.equal(await (await api.downloadHandoverReport("p/x","t?x")).text(),"# Safe");
    assert.deepEqual(calls.map(c=>c[0]),["/api/v1/projects/p%2Fx/agent-tasks/t%3Fx/handover-report","/api/v1/projects/p%2Fx/agent-tasks/t%3Fx/handover-report/markdown"]);
    assert.equal(calls[1][1].credentials,"same-origin");assert.equal(calls[1][1].cache,"no-store");
    globalThis.fetch=async()=>new Response(JSON.stringify({error:{code:"task_scope_changed"}}),{status:409});
    await assert.rejects(api.downloadHandoverReport("p","t"),e=>e.code==="task_scope_changed");
    globalThis.fetch=async()=>new Response("<html>login</html>",{headers:{"Content-Type":"text/html"}});
    await assert.rejects(api.downloadHandoverReport("p","t"));
  } finally {globalThis.fetch=original;}
});

const archivePlan={id:"a".repeat(32),task_id:"t",project_id:"p",revision:3,policy_revision:2,
  content_sha256:"b".repeat(64),state:"preview",task_state:"waiting_confirmation",decision:null,
  operation_id:null,executable:false,has_unresolved:false,
  items:[{file_id:"f",version_id:"v",source_directory_id:"source",target_directory_id:"target",
    name:"<img onerror=bad> sample",target_name:"Deployment",reason:"<script>not approval</script>",state:"proposed"}]};
const archiveReceipt={plan_id:archivePlan.id,revision:3,operation_id:"c".repeat(32),status:"executed",moved_count:1,audit_id:"d".repeat(32)};
const descendants=n=>[n,...n.children.flatMap(descendants)];
function clickNamed(view,box,name) {
  const button=buttons(view.get(box)).find(b=>b.textContent===name);
  assert.ok(button,`missing visible action: ${name}`); return button.fire();
}
async function archiveSetup(overrides={},initial=archivePlan,role="editor") {
  let current=structuredClone(initial),committed=false; const events=[];
  const view=await setup({getAgentTask:async()=>({...task,state:committed?"completed":current.task_state}),
    getAgentResult:async()=>({state:"completed",kind:"archive_operation",model_calls:0,result:null,error_code:null,archive_operation:archiveReceipt}),
    getArchivePlan:async(...args)=>{events.push(["plan",...args]);return structuredClone(current);},
    listDirectories:async()=>({items:[{id:"source",name:"Original directory"},{id:"target",name:"Deployment"}]}),
    decideArchivePlan:async(...args)=>{events.push(["decision",...args]); const decision=args[2].decision;
      current={...current,decision,state:decision==="confirm"?"confirmed":"rejected",
        task_state:decision==="confirm"?"waiting_execution":"waiting_confirmation",executable:decision==="confirm",
        operation_id:decision==="confirm"?archiveReceipt.operation_id:null};return structuredClone(current);},
    executeArchivePlan:async(...args)=>{events.push(["execute",...args]);committed=true;return {...archiveReceipt,items:[]};},
    getArchiveReceipt:async(...args)=>{events.push(["receipt",...args]);return archiveReceipt;},...overrides});
  view.panel.setProject("p",role); await view.panel.view("t"); return {...view,events};
}
async function openArchive(view) { await clickNamed(view,"agent-detail","View archive plan"); }
function archiveConsent(view) {
  const checkbox=descendants(view.get("agent-archive")).find(n=>n.tagName==="INPUT" && n.type==="checkbox");
  assert.ok(checkbox,"confirmation needs a visible unchecked checkbox"); return checkbox;
}

test("archive review displays bound items as text and requires a separate explicit confirmation and execution",async()=>{
  const view=await archiveSetup(); assert.equal(view.events.length,0); await openArchive(view);
  const box=view.get("agent-archive"), content=text(box);
  for(const marker of ["Original directory","Deployment","<img onerror=bad>","<script>not approval</script>","Version v","Revision 3"]) assert.ok(content.includes(marker));
  assert.equal(descendants(box).filter(n=>["SCRIPT","IMG"].includes(n.tagName)).length,0);
  assert.equal(archiveConsent(view).checked,false);
  await clickNamed(view,"agent-archive","Confirm this revision");
  assert.equal(view.events.filter(x=>x[0]==="decision").length,0);
  archiveConsent(view).checked=true; await clickNamed(view,"agent-archive","Confirm this revision");
  assert.deepEqual(view.events.find(x=>x[0]==="decision"),["decision","p","t",{
    plan_id:"a".repeat(32),revision:3,content_sha256:"b".repeat(64),decision:"confirm"}]);
  assert.equal(view.events.filter(x=>x[0]==="execute").length,0);
  await clickNamed(view,"agent-archive","Execute confirmed revision");
  assert.deepEqual(view.events.find(x=>x[0]==="execute"),["execute","p","t",{
    plan_id:"a".repeat(32),revision:3,operation_id:"c".repeat(32)}]);
  assert.match(text(view.get("agent-archive")),/1 files/);
  assert.equal(buttons(view.get("agent-archive")).filter(b=>/Confirm|Execute/.test(b.textContent)).length,0);
});

test("archive reject cannot execute and unresolved targets cannot be confirmed",async()=>{
  const view=await archiveSetup();await openArchive(view);
  await clickNamed(view,"agent-archive","Reject this revision");
  assert.equal(view.events.find(x=>x[0]==="decision")[3].decision,"reject");
  assert.equal(buttons(view.get("agent-archive")).filter(b=>/Confirm|Execute/.test(b.textContent)).length,0);
  const unresolved=await archiveSetup({}, {...archivePlan,has_unresolved:true,items:[{...archivePlan.items[0],target_directory_id:null,target_name:null,state:"needs_review"}]});
  await openArchive(unresolved);
  assert.match(text(unresolved.get("agent-archive")),/needs review|Needs review/i);
  assert.equal(buttons(unresolved.get("agent-archive")).filter(b=>/Confirm|Execute/.test(b.textContent)).length,0);
});

test("archive readers and terminal tasks have no write controls",async()=>{
  for(const [role,state] of [["reader","waiting_confirmation"],["editor","cancelled"],["editor","interrupted"]]) {
    const view=await archiveSetup({}, {...archivePlan,task_state:state},role); await openArchive(view);
    assert.equal(buttons(view.get("agent-archive")).filter(b=>/Confirm|Reject|Execute/.test(b.textContent)).length,0);
  }
});

test("archive delayed loads and stale actions cannot survive task refresh logout or role change",async()=>{
  for(const invalidate of [v=>v.panel.invalidate(),v=>v.panel.setProject(null),v=>v.panel.setProject("other","editor"),v=>v.panel.setProject("p","reader"),v=>v.panel.view("new")]) {
    const late=deferred(); const view=await archiveSetup(); await openArchive(view);
    archiveConsent(view).checked=true; const old=buttons(view.get("agent-archive")).find(b=>b.textContent==="Confirm this revision");
    view.panel.api.getArchivePlan=()=>late.promise;
    const loading=clickNamed(view,"agent-detail","View archive plan");
    await invalidate(view);late.resolve(archivePlan);await loading;await old.fire();
    assert.equal(view.get("agent-archive").children.length,0);
    assert.equal(view.events.filter(x=>x[0]==="decision").length,0);
  }
});

test("archive write lock survives invalidation and duplicate clicks until the actual request settles",async()=>{
  const late=deferred(); let count=0;
  const view=await archiveSetup({decideArchivePlan:()=>{count++;return late.promise;}});await openArchive(view);
  archiveConsent(view).checked=true;const old=buttons(view.get("agent-archive")).find(b=>b.textContent==="Confirm this revision");
  const sending=old.fire();await old.fire();view.panel.invalidate();view.panel.setProject("p","editor");
  await view.panel.view("t");await old.fire();assert.equal(count,1);
  assert.equal(view.get("agent-create").disabled,true);
  late.resolve({...archivePlan,state:"confirmed",task_state:"waiting_execution",operation_id:archiveReceipt.operation_id,executable:true});
  await sending;assert.equal(view.get("agent-archive").children.length,0);
  assert.equal(view.get("agent-create").disabled,false);
});

test("cancellation remains locked across invalidation until the real request settles",async()=>{
  for(const fails of [false,true]) {
    const late=deferred();let cancellations=0;
    const view=await archiveSetup({cancelAgentTask:()=>{cancellations++;return late.promise;}});
    const cancelling=clickNamed(view,"agent-detail","Cancel task");
    view.panel.invalidate();view.panel.setProject("p","editor");
    await view.panel.view("t");
    if(view.get("agent-detail").children.length) {
      await openArchive(view);archiveConsent(view).checked=true;
      await clickNamed(view,"agent-archive","Confirm this revision");
    }
    assert.equal(view.events.filter(x=>x[0]==="decision").length,0);
    assert.equal(view.get("agent-create").disabled,true);
    assert.equal(cancellations,1);
    if(fails) late.reject(new Error("cancel response lost")); else late.resolve({...task,state:"cancelled"});
    await cancelling;
    assert.equal(view.get("agent-archive").children.length,0);
    assert.equal(view.get("agent-create").disabled,false);
    await view.panel.view("t");assert.ok(view.get("agent-detail").children.length);
  }
});

test("archive conflict clears sensitive snapshot and invalidates held confirmation buttons",async()=>{
  let writes=0;
  const view=await archiveSetup({decideArchivePlan:async()=>{writes++;throw new Error("archive_plan_changed");}});await openArchive(view);
  archiveConsent(view).checked=true;const old=buttons(view.get("agent-archive")).find(b=>b.textContent==="Confirm this revision");
  await old.fire();await old.fire();
  assert.equal(writes,1);
  assert.equal(view.get("agent-archive").children.length,0);
  assert.match(text(view.get("agent-archive-status")),/unknown|check|refresh/i);
  assert.doesNotMatch(text(view.get("agent-archive")),/not approval/);
});

test("archive receipt remains readable after revoked plan and ignores non-receipt fields",async()=>{
  const view=await archiveSetup({getArchiveReceipt:async()=>({...archiveReceipt,items:[{name:"PRIVATE-RECEIPT-ITEM"}],answer:"PRIVATE-ANSWER"})});
  await openArchive(view);
  view.panel.api.getArchivePlan=async()=>{throw new Error("task_scope_changed");};
  await openArchive(view);
  assert.equal(view.get("agent-archive").children.length,0);
  await clickNamed(view,"agent-detail","Check archive receipt");
  const content=text(view.get("agent-archive"));
  for(const marker of [archiveReceipt.plan_id,archiveReceipt.operation_id,archiveReceipt.audit_id,"Revision 3","1 files","executed"]) assert.ok(content.includes(marker));
  assert.doesNotMatch(content,/PRIVATE|not approval/);
  assert.equal(buttons(view.get("agent-archive")).length,0);
});

test("archive lost execution response offers only explicit read recovery and no automatic write retry",async()=>{
  let writes=0;const confirmed={...archivePlan,state:"confirmed",decision:"confirm",task_state:"waiting_execution",executable:true,operation_id:archiveReceipt.operation_id};
  const view=await archiveSetup({executeArchivePlan:async()=>{writes++;throw new Error("response lost");}},confirmed);await openArchive(view);
  await clickNamed(view,"agent-archive","Execute confirmed revision");assert.equal(writes,1);
  assert.equal(view.events.filter(x=>x[0]==="receipt").length,0);
  await clickNamed(view,"agent-detail","Check archive receipt");assert.equal(writes,1);
  assert.equal(view.events.filter(x=>x[0]==="receipt").length,1);
  assert.match(text(view.get("agent-archive")),new RegExp(archiveReceipt.operation_id));
  assert.equal(buttons(view.get("agent-archive")).length,0);
});

test("archive stale load cannot overwrite a newer preview and reloading resets consent",async()=>{
  const view=await archiveSetup();await openArchive(view);archiveConsent(view).checked=true;
  const first=deferred(),second=deferred();let reads=0;
  view.panel.api.getArchivePlan=()=>++reads===1?first.promise:second.promise;
  const older=clickNamed(view,"agent-detail","View archive plan"),newer=clickNamed(view,"agent-detail","View archive plan");
  second.resolve({...archivePlan,revision:4,content_sha256:"e".repeat(64)});await newer;
  first.resolve(archivePlan);await older;
  assert.match(text(view.get("agent-archive")),/Revision 4/);assert.equal(archiveConsent(view).checked,false);
});

test("archive HTTP adapter preserves encoded identity and exact bound write payloads",async()=>{
  const api=await import(new URL("../../resources/js/api.js",import.meta.url));
  assert.equal(typeof api.getArchivePlan,"function");const original=globalThis.fetch,calls=[];
  globalThis.fetch=async(...args)=>{calls.push(args);return new Response(JSON.stringify({data:archivePlan}));};
  try {
    await api.getArchivePlan("p/x","t?x");
    const command={plan_id:archivePlan.id,revision:3,content_sha256:archivePlan.content_sha256,decision:"confirm"};
    await api.decideArchivePlan("p/x","t?x",command);
    await api.executeArchivePlan("p/x","t?x",{plan_id:archivePlan.id,revision:3,operation_id:archiveReceipt.operation_id});
    await api.getArchiveReceipt("p/x","t?x");
    assert.deepEqual(calls.map(c=>[c[0],c[1].method,c[1].credentials]),[
      ["/api/v1/projects/p%2Fx/agent-tasks/t%3Fx/archive-plan","GET","same-origin"],
      ["/api/v1/projects/p%2Fx/agent-tasks/t%3Fx/archive-plan/decision","POST","same-origin"],
      ["/api/v1/projects/p%2Fx/agent-tasks/t%3Fx/archive-plan/execute","POST","same-origin"],
      ["/api/v1/projects/p%2Fx/agent-tasks/t%3Fx/archive-plan/result","GET","same-origin"]]);
    assert.deepEqual(JSON.parse(calls[1][1].body),command);assert.equal(calls[0][1].body,undefined);assert.equal(calls[3][1].body,undefined);
  } finally {globalThis.fetch=original;}
});

test("creation requires explicit exact-version consent and never claims execution",async()=>{
  const view=await setup(); const {panel,get,calls}=view;
  panel.addVersion("p",{...file,remote_ai_policy:"internal_only"},version);
  panel.addVersion("p",file,{...version,id:"old"});
  panel.addVersion("p",file,{...version,remote_ai_approved:false});
  panel.addVersion("other",file,version);
  assert.equal(get("agent-scope").children.length,0);
  await get("agent-form").fire("submit"); assert.equal(calls.length,0);
  draft(view); get("agent-consent").checked=false;
  await get("agent-form").fire("submit"); assert.equal(calls.length,0);
  get("agent-consent").checked=true;
  await get("agent-form").fire("submit");
  assert.deepEqual(calls.filter(c=>c[0]==="create"),[["create","p",{question:"Check public sample",versions:[{file_id:"f",version_id:"v"}],confirm_remote_ai:true}]]);
  assert.match(text(get("agent-create-status")),/pending|waiting/i);
  assert.equal(get("agent-consent").checked,false);
  assert.equal(get("agent-question").value,"");
});

test("edits reset consent, UTF-8 limit blocks sends, and duplicate clicks cannot recreate",async()=>{
  const late=deferred(); let creates=0;
  const view=await setup({createAgentTask:()=>{creates++;return late.promise;}}); const {get,panel}=view;
  draft(view); get("agent-question").fire("input"); assert.equal(get("agent-consent").checked,false);
  get("agent-question").value="文".repeat(1334); get("agent-consent").checked=true;
  await get("agent-form").fire("submit"); assert.equal(creates,0);
  draft(view); const pending=get("agent-form").fire("submit");
  await get("agent-form").fire("submit"); assert.equal(creates,1);
  late.reject(new Error("response lost")); await pending;
  assert.match(text(get("agent-create-status")),/unknown|uncertain/i);
  await panel.refresh(); get("agent-consent").checked=true;
  await get("agent-form").fire("submit"); assert.equal(creates,1);
  get("agent-new-draft").fire(); assert.equal(get("agent-question").value,"");
  assert.equal(get("agent-scope").children.length,0);
});

test("task list uses cursor and fences late list responses and old buttons",async()=>{
  const late=deferred(); const view=await setup(); const {panel,get,calls}=view;
  await panel.refresh(); const open=buttons(get("agent-list"))[0];
  await get("agent-next").fire(); assert.deepEqual(calls.at(-1),["list","p","t"]);
  panel.api.listAgentTasks=()=>late.promise;
  const pending=panel.refresh(); panel.setProject("other"); late.resolve({items:[task],next_before:null}); await pending;
  await open.fire(); assert.equal(calls.filter(c=>c[0]==="get").length,0);
  assert.equal(get("agent-list").children.length,0);
});

test("file and project invalidation cannot unlock an in-flight non-idempotent create",async()=>{
  const late=deferred(); let creates=0;
  const view=await setup({createAgentTask:()=>{creates++;return late.promise;}});
  const {panel,get}=view;
  draft(view); const pending=get("agent-form").fire("submit");
  panel.invalidate(); draft(view); get("agent-new-draft").fire();
  draft(view); const duplicate=get("agent-form").fire("submit"); assert.equal(creates,1); await duplicate;
  panel.setProject("other"); panel.setProject("p"); draft(view);
  await get("agent-form").fire("submit"); assert.equal(creates,1);
  late.resolve(task); await pending;
  draft(view); await get("agent-form").fire("submit"); assert.equal(creates,1);
  assert.match(text(get("agent-create-status")),/unknown|uncertain/i);
  await panel.refresh(); get("agent-new-draft").fire(); draft(view);
  await get("agent-form").fire("submit"); assert.equal(creates,2);
});

test("completed answers and fixed sources are text only and rechecked on access",async()=>{
  const {panel,get,calls}=await setup({getAgentTask:async()=>({...task,state:"completed"})});
  await panel.refresh(); await buttons(get("agent-list"))[0].fire();
  assert.match(text(get("agent-detail")),/<script>answer<\/script>/);
  const source=buttons(get("agent-detail")).find(b=>b.textContent==="Read cited source");
  await source.fire();
  assert.deepEqual(calls.filter(c=>c[0]==="source"),[["source","p","f","v","c"]]);
  assert.equal(calls.filter(c=>c[0]==="result").length,2);
  assert.match(text(get("agent-source")),/historical|Historical/);
  assert.match(text(get("agent-source")),/physical page 2/);
  assert.equal(get("agent-source").children.at(-1).href,"/api/p/f/v/content#page=2");
  panel.api.getAgentResult=async()=>{throw new Error("scope changed");};
  await source.fire(); assert.equal(get("agent-source").children.length,0);
  assert.doesNotMatch(text(get("agent-detail")),/<script>answer/);
});

test("cancel response loss requires refresh and cannot display stale results",async()=>{
  let cancels=0; const {panel,get}=await setup({cancelAgentTask:async()=>{cancels++;throw new Error("response lost");}});
  await panel.refresh(); await buttons(get("agent-list"))[0].fire();
  const cancel=buttons(get("agent-detail")).find(b=>b.textContent==="Cancel task");
  await cancel.fire(); await cancel.fire(); assert.equal(cancels,1);
  assert.match(text(get("agent-detail-status")),/Refresh|refresh/);
  assert.equal(get("agent-detail").children.length,0);
});

test("logout and data invalidation erase draft, answer and fence pending source",async()=>{
  const late=deferred(); const view=await setup({getAgentTask:async()=>({...task,state:"completed"}),readDocument:()=>late.promise});
  const {panel,get}=view; draft(view);
  await panel.refresh(); await buttons(get("agent-list"))[0].fire();
  const source=buttons(get("agent-detail")).find(b=>b.textContent==="Read cited source");
  const pending=source.fire(); await Promise.resolve(); panel.setProject(null);
  late.resolve({chunk:{text:"private"}}); await pending;
  for(const id of ["scope","list","detail","source"]) assert.equal(get("agent-"+id).children.length,0);
  assert.equal(get("agent-question").value,""); assert.equal(get("agent-consent").checked,false);
  await get("agent-form").fire("submit"); assert.equal(get("agent-create").disabled,true);
});

function auditButton(get) {
  const button=buttons(get("agent-detail")).find(b=>/Load.*tool calls/.test(b.textContent));
  assert.ok(button,"task needs an explicit tool-call summary action");
  return button;
}

test("waiting archive tasks remain cancellable without automatic execution",async()=>{
  for(const state of ["waiting_confirmation","waiting_execution"]) {
    const {panel,get,calls}=await setup({getAgentTask:async()=>({...task,state})});
    await panel.view("t");
    assert.doesNotMatch(text(get("agent-detail-status")),/Unknown state/);
    assert.match(text(get("agent-detail-status")),/waiting|confirmation|execution/i);
    const cancel=buttons(get("agent-detail")).find(b=>b.textContent==="Cancel task");
    assert.ok(cancel);
    assert.deepEqual(calls,[]);
    await cancel.fire();
    assert.equal(calls.filter(c=>c[0]==="cancel").length,1);
  }
});

test("completed archive receipt is not described as a model answer or rendered as citations",async()=>{
  const receipt={operation_id:"a".repeat(32),plan_id:"b".repeat(32),revision:1,status:"executed",moved_count:2,audit_id:"c".repeat(32)};
  const {panel,get}=await setup({getAgentTask:async()=>({...task,state:"completed"}),
    getAgentResult:async()=>({state:"completed",kind:"archive_operation",model_calls:0,result:null,error_code:null,archive_operation:receipt})});
  await panel.view("t");
  assert.match(text(get("agent-detail-status")),/archive|Archive/);
  assert.doesNotMatch(text(get("agent-detail-status")),/answer draft/i);
  assert.match(text(get("agent-detail")),/2 files/);
  assert.match(text(get("agent-detail")),new RegExp(receipt.operation_id));
  assert.equal(buttons(get("agent-detail")).filter(b=>/source|execute/i.test(b.textContent)).length,0);
});

test("audit uses an encoded same-origin GET and never submits a tool operation",async()=>{
  const api=await import(new URL("../../resources/js/api.js",import.meta.url));
  assert.equal(typeof api.getAgentToolCalls,"function");
  const original=globalThis.fetch; const calls=[];
  globalThis.fetch=async (...args)=>{calls.push(args);return new Response(JSON.stringify({request_id:"r",data:audit}));};
  try {
    assert.deepEqual(await api.getAgentToolCalls("p/x","t?x"),audit);
    assert.equal(calls.length,1);
    assert.equal(calls[0][0],"/api/v1/projects/p%2Fx/agent-tasks/t%3Fx/tool-calls");
    assert.equal(calls[0][1].method,"GET");
    assert.equal(calls[0][1].credentials,"same-origin");
    assert.equal(calls[0][1].body,undefined);
  } finally {globalThis.fetch=original;}
});

test("audit is user-loaded, expandable, redacted and distinguishes zero time from unknown",async()=>{
  const {panel,get,calls}=await setup();
  await panel.view("t"); assert.equal(calls.filter(c=>c[0]==="audit").length,0);
  await auditButton(get).fire();
  assert.deepEqual(calls.filter(c=>c[0]==="audit"),[["audit","p","t"]]);
  assert.equal(get("agent-audit").children[0].tagName,"DETAILS");
  assert.equal(get("agent-audit").children[0].children[0].tagName,"SUMMARY");
  assert.match(text(get("agent-audit")),/0 ms/);
  assert.match(text(get("agent-audit")),/keyword.*6/);
  assert.match(text(get("agent-audit-status")),/backend requests.*not.*MCP/i);
  panel.api.getAgentToolCalls=async()=>({...audit,items:[{...audit.items[0],tool:"read_document_version",
    request_id:"<img onerror=bad>",status:"unknown",duration_ms:null,parameters:{selector:"chunk",query:"SECRET",body:"PRIVATE"}}]});
  await auditButton(get).fire();
  assert.match(text(get("agent-audit")),/unknown.*not confirmed/i);
  assert.match(text(get("agent-audit")),/not recorded/i);
  assert.match(text(get("agent-audit")),/chunk/);
  assert.match(text(get("agent-audit")),/<img onerror=bad>/);
  assert.doesNotMatch(text(get("agent-audit")),/SECRET|PRIVATE|0 ms|success/i);
});

test("empty audit never means the task succeeded or no model was called",async()=>{
  const {panel,get}=await setup({getAgentToolCalls:async()=>({...audit,items:[]})});
  await panel.view("t"); await auditButton(get).fire();
  assert.equal(get("agent-audit").children.length,0);
  assert.match(text(get("agent-audit-status")),/No recorded backend requests/);
  assert.match(text(get("agent-audit-status")),/not.*completion|does not prove/i);
});

test("audit refresh clears old rows and latest response wins",async()=>{
  const {panel,get}=await setup(); await panel.view("t");
  const load=auditButton(get); await load.fire();
  const first=deferred(),second=deferred(); let count=0;
  panel.api.getAgentToolCalls=()=>++count===1 ? first.promise : second.promise;
  const a=load.fire(); assert.equal(get("agent-audit").children.length,0);
  const b=load.fire(); second.resolve({...audit,items:[]}); await b;
  first.resolve(audit); await a;
  assert.equal(get("agent-audit").children.length,0);
  assert.match(text(get("agent-audit-status")),/No recorded/);
});

test("audit late responses and detached buttons cannot survive view or session invalidation",async()=>{
  for(const change of [p=>p.invalidate(),p=>p.setProject(null),p=>p.setProject("other"),p=>p.refresh(),p=>p.view("new")]) {
    const {panel,get}=await setup(); await panel.view("t"); const load=auditButton(get); await load.fire();
    const late=deferred(); let requests=0;
    panel.api.getAgentToolCalls=()=>{requests++;return late.promise;};
    const pending=load.fire(); await change(panel);
    late.resolve(audit); await pending; await load.fire();
    assert.equal(requests,1); assert.equal(get("agent-audit").children.length,0);
    assert.doesNotMatch(text(get("agent-audit-status")),/1 backend/);
  }
});

test("audit denial clears answer, source and audit without automatic retries",async()=>{
  const {panel,get}=await setup({getAgentTask:async()=>({...task,state:"completed"})});
  await panel.view("t"); const load=auditButton(get); await load.fire();
  await buttons(get("agent-detail")).find(b=>b.textContent==="Read cited source").fire();
  let requests=0;
  panel.api.getAgentToolCalls=async()=>{requests++;throw new Error("task_scope_changed");};
  await load.fire(); await load.fire();
  assert.equal(requests,1);
  for(const id of ["detail","source","audit"]) assert.equal(get("agent-"+id).children.length,0);
  assert.match(text(get("agent-detail-status")),/task_scope_changed/);
});
