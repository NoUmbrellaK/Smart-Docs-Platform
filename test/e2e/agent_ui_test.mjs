import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

class Node {
  constructor() { this.children=[]; this.listeners={}; this.value=""; this.checked=false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  addEventListener(type, fn) { this.listeners[type]=fn; }
  fire(type="click") { return this.listeners[type]({preventDefault() {}}); }
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
