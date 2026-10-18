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
globalThis.document={createElement: () => new Node()};
const text=n => [n.textContent || "", ...n.children.map(text)].join(" ");
const buttons=n => [...(n.listeners.click ? [n] : []), ...n.children.flatMap(buttons)];
const deferred=() => { let resolve,reject; const promise=new Promise((y,n) => {resolve=y;reject=n;}); return {promise,resolve,reject}; };
const file={id:"f",current_version_id:"v",name:"<img onerror=bad>",remote_ai_policy:"remote_ai_allowed",deleted:false};
const version={id:"v",remote_ai_approved:true,version_number:1};
const task={id:"t",project_id:"p",state:"pending",versions:[{file_id:"f",version_id:"v"}],created_at:"2026-10-18",updated_at:"2026-10-18",confirmed_at:"2026-10-18"};
const result={state:"completed",model_calls:2,error_code:null,result:{answer:"<script>answer</script>",citations:[{file_id:"f",version_id:"v",chunk_id:"c"}]}};
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
