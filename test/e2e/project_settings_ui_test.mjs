import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import test from "node:test";

class Node {
  constructor(tag="div") {Object.assign(this,{tag,children:[],listeners:{},value:"",checked:false});}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(key,value){this[key]=value;}
  fire(type="click"){return this.listeners[type]?.({preventDefault(){}});}
}
globalThis.document={createElement:tag=>new Node(tag)};
const all=n=>[n,...n.children.flatMap(all)];
const text=n=>all(n).map(x=>x.textContent||"").join(" ");
const deferred=()=>{let resolve,reject;const promise=new Promise((y,n)=>{resolve=y;reject=n;});return {promise,resolve,reject};};
const checklist={revision:3,remote_ai_approved:true,items:[{key:"deploy",title:"<script>Deploy</script>",description:"Rollback",required:true}]};
const policy={revision:5,remote_ai_approved:true,targets:[{directory_id:"d",name:"<img>Deployment"}]};
const directories={items:[{id:"root",name:"Project root",parent_id:null},{id:"d",name:"<img>Deployment",parent_id:"root"}]};
async function setup(overrides={}) {
  const url=new URL("../../resources/js/project-settings.js",import.meta.url);
  assert.ok(existsSync(url),"project settings editor is not implemented");
  const {ProjectSettingsPanel}=await import(url.href);
  const nodes=new Map(),calls=[],changed=[];
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
  const api={getHandoverChecklist:async()=>structuredClone(checklist),getArchivePolicy:async()=>structuredClone(policy),
    listDirectories:async()=>structuredClone(directories),
    setHandoverChecklist:async(p,body)=>{calls.push(["checklist",p,body]);return {revision:4,items:body.items,remote_ai_approved:body.confirm_remote_ai};},
    setArchivePolicy:async(p,body)=>{calls.push(["archive",p,body]);return {revision:6,targets:body.targets,remote_ai_approved:body.confirm_remote_ai};},...overrides};
  const panel=new ProjectSettingsPanel(get,api,e=>e.message,p=>changed.push(p));panel.setProject("p","admin");
  return {panel,get,calls,changed};
}
const field=(v,name)=>all(v.get("settings-checklist-rows")).find(n=>n.name===name);
async function load(v,kind){await v.get(`settings-${kind}-refresh`).fire();}
async function save(v,kind){await v.get(`settings-${kind}-form`).fire("submit");}

test("settings load as unapproved drafts and save only exact revision and explicit approval",async()=>{
  const v=await setup();await load(v,"checklist");
  assert.equal(v.get("settings-checklist-approval").checked,false);
  assert.equal(field(v,"title").value,"<script>Deploy</script>");
  assert.match(text(v.get("settings-checklist-status")),/3.*approved/i);
  field(v,"title").value="Updated deployment";field(v,"title").fire("input");
  v.get("settings-checklist-approval").checked=true;
  await save(v,"checklist");
  assert.deepEqual(v.calls,[["checklist","p",{expected_revision:3,confirm_remote_ai:true,
    items:[{key:"deploy",title:"Updated deployment",description:"Rollback",required:true}]}]]);
  assert.equal(v.get("settings-checklist-approval").checked,false);
  assert.equal(v.get("settings-checklist-editor").disabled,true,"must reload before another revision");
  assert.deepEqual(v.changed,["p","p"]);
  await load(v,"archive");
  assert.equal(v.get("settings-archive-approval").checked,false);
  assert.equal(all(v.get("settings-archive-rows")).filter(n=>["script","img"].includes(n.tag)).length,0);
  await save(v,"archive");
  assert.deepEqual(v.calls[1],["archive","p",{expected_revision:5,confirm_remote_ai:false,targets:policy.targets}]);
});

test("checklist edits including add remove and required revoke approval; empty lists can be saved",async()=>{
  const v=await setup();await load(v,"checklist");
  for(const [name,event] of [["key","input"],["title","input"],["description","input"],["required","change"]]) {
    v.get("settings-checklist-approval").checked=true;field(v,name).fire(event);
    assert.equal(v.get("settings-checklist-approval").checked,false);
  }
  v.get("settings-checklist-approval").checked=true;await v.get("settings-checklist-add").fire();
  assert.equal(v.get("settings-checklist-approval").checked,false);
  let remove=all(v.get("settings-checklist-rows")).filter(n=>n.tag==="button");assert.equal(remove.length,2);
  v.get("settings-checklist-approval").checked=true;await remove[1].fire();
  assert.equal(v.get("settings-checklist-approval").checked,false);
  await remove[0].fire();await save(v,"checklist");
  assert.deepEqual(v.calls[0][2],{expected_revision:3,confirm_remote_ai:false,items:[]});
  await load(v,"archive");
  const selected=all(v.get("settings-archive-rows")).find(n=>n.tag==="input"&&n.checked);
  v.get("settings-archive-approval").checked=true;selected.checked=false;selected.fire("change");
  assert.equal(v.get("settings-archive-approval").checked,false);
  await save(v,"archive");assert.deepEqual(v.calls[1][2].targets,[]);
});

test("invalid UTF-8 sizes keys duplicates and controls are rejected without writes",async()=>{
  for(const [name,value] of [["key","Bad Key"],["title"," "],["title","文".repeat(67)],
    ["description","文".repeat(334)],["description","newline\n"],["title","\u007f"]]) {
    const v=await setup();await load(v,"checklist");field(v,name).value=value;await save(v,"checklist");assert.equal(v.calls.length,0);
  }
  const v=await setup();await load(v,"checklist");await v.get("settings-checklist-add").fire();
  const keys=all(v.get("settings-checklist-rows")).filter(n=>n.name==="key");keys[1].value="deploy";
  const titles=all(v.get("settings-checklist-rows")).filter(n=>n.name==="title");titles[1].value="Duplicate";
  await save(v,"checklist");assert.equal(v.calls.length,0);
});

test("non-admin callers and stale loads or detached editors never gain write authority",async()=>{
  for(const change of [v=>v.panel.setProject(null),v=>v.panel.setProject("q","admin"),v=>v.panel.setProject("p","reader")]) {
    const late=deferred();let reads=0;const v=await setup({getHandoverChecklist:()=>{reads++;return late.promise;}});
    const pending=load(v,"checklist");assert.equal(reads,1);change(v);late.resolve(checklist);await pending;
    assert.equal(v.get("settings-checklist-rows").children.length,0);await save(v,"checklist");assert.equal(v.calls.length,0);
  }
  const v=await setup();await load(v,"checklist");const remove=all(v.get("settings-checklist-rows")).find(n=>n.tag==="button");
  await load(v,"checklist");await remove.fire();assert.equal(v.get("settings-checklist-rows").children.length,1);
  v.panel.setProject("p","reader");await load(v,"checklist");await save(v,"checklist");
  assert.equal(v.get("settings-checklist-form").hidden,true);assert.equal(v.calls.length,0);
});

test("late reads cannot overwrite newer snapshots and failed reads clear all old fields",async()=>{
  const late=deferred();let reads=0;const v=await setup({getHandoverChecklist:()=>++reads===1?late.promise:Promise.resolve({...checklist,revision:8})});
  const old=load(v,"checklist");await load(v,"checklist");late.resolve(checklist);await old;
  await save(v,"checklist");assert.equal(v.calls[0][2].expected_revision,8);
  await load(v,"archive");v.panel.api.getArchivePolicy=async()=>{throw new Error("denied");};await load(v,"archive");
  assert.equal(v.get("settings-archive-rows").children.length,0);await save(v,"archive");assert.equal(v.calls.length,1);
});

test("write locks survive project resets and uncertain writes require explicit reread",async()=>{
  const late=deferred();let writes=0;const v=await setup({setHandoverChecklist:()=>{writes++;return late.promise;}});
  await load(v,"checklist");const pending=save(v,"checklist");
  v.panel.setProject("q","admin");await load(v,"checklist");await save(v,"checklist");assert.equal(writes,1);
  assert.equal(v.get("settings-checklist-refresh").disabled,true);
  late.reject(new Error("response lost"));await pending;
  assert.deepEqual(v.changed,["p","p"]);assert.equal(v.get("settings-checklist-refresh").disabled,false);
  assert.equal(v.get("settings-checklist-rows").children.length,0);
  await save(v,"checklist");assert.equal(writes,1);
  const conflict=await setup({setArchivePolicy:async()=>{throw new Error("archive_policy_changed");}});
  await load(conflict,"archive");await save(conflict,"archive");
  assert.equal(conflict.get("settings-archive-rows").children.length,0);
  assert.match(text(conflict.get("settings-archive-status")),/refresh|reload/i);
});

test("settings PUT adapters bind encoded project and full revision-checked commands",async()=>{
  const api=await import("../../resources/js/api.js");assert.equal(typeof api.setHandoverChecklist,"function");assert.equal(typeof api.setArchivePolicy,"function");
  const original=globalThis.fetch,calls=[];globalThis.fetch=async(...args)=>{calls.push(args);return new Response(JSON.stringify({data:{revision:4}}));};
  try {
    const commands=[{expected_revision:3,confirm_remote_ai:false,items:[]},{expected_revision:5,confirm_remote_ai:true,targets:policy.targets}];
    await api.setHandoverChecklist("p/x",commands[0]);await api.setArchivePolicy("p/x",commands[1]);
    assert.deepEqual(calls.map(c=>c[0]),["/api/v1/projects/p%2Fx/handover-checklist","/api/v1/projects/p%2Fx/archive-policy"]);
    calls.forEach(([,o],i)=>{assert.equal(o.method,"PUT");assert.equal(o.credentials,"same-origin");assert.deepEqual(JSON.parse(o.body),commands[i]);});
  } finally {globalThis.fetch=original;}
});
