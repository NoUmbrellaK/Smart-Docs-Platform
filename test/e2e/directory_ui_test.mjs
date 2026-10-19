import assert from "node:assert/strict";
import test from "node:test";
import * as settings from "../../resources/js/project-settings.js";

class Node {
  constructor(tag="div") { Object.assign(this,{tag,children:[],listeners:{},value:"",checked:false}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  addEventListener(type,fn) { this.listeners[type]=fn; }
  fire(type="click") { return this.listeners[type]?.({preventDefault(){}}); }
}
globalThis.document={createElement:tag=>new Node(tag)};
const all=n=>[n,...n.children.flatMap(all)];
const text=n=>all(n).map(x=>x.textContent||"").join(" ");
const deferred=()=>{let resolve,reject;const promise=new Promise((y,n)=>{resolve=y;reject=n;});return {promise,resolve,reject};};
const snapshot=[{id:"root",project_id:"p",parent_id:null,name:"Project root"},
  {id:"d",project_id:"p",parent_id:"root",name:"<img>Deployment"}];
function setup(overrides={}) {
  assert.equal(typeof settings.DirectoryPanel,"function","directory administration UI is missing");
  const nodes=new Map(),calls=[],changed=[],loaded=[];let rows=structuredClone(snapshot);
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
  const api={listDirectories:async p=>{calls.push(["read",p]);return {items:structuredClone(rows)};},
    createDirectory:async(p,parent,name)=>{calls.push(["create",p,parent,name]);const row={id:"new",project_id:p,parent_id:parent,name};rows.push(row);return row;},
    renameDirectory:async(p,id,name)=>{calls.push(["rename",p,id,name]);rows.find(d=>d.id===id).name=name;return rows.find(d=>d.id===id);},...overrides};
  const panel=new settings.DirectoryPanel(get,api,e=>e.message,p=>changed.push(p),(p,items)=>loaded.push([p,items]));
  panel.setProject("p","admin");return {panel,get,calls,changed,loaded};
}
const load=v=>v.get("settings-directory-refresh").fire();
const create=v=>v.get("settings-directory-form").fire("submit");
const writes=v=>v.calls.filter(c=>c[0]!=="read");
const renameForm=v=>all(v.get("settings-directory-rows")).find(n=>n.tag==="form");

test("directory create binds selected parent and refreshes authority without replaying the write",async()=>{
  const v=setup();assert.equal(v.get("settings-directory-editor").disabled,true);await create(v);assert.equal(writes(v).length,0);
  await load(v);v.get("settings-directory-parent").value="d";v.get("settings-directory-name").value="  Child  ";await create(v);
  assert.deepEqual(writes(v),[["create","p","d","Child"]]);
  assert.equal(v.loaded.at(-1)[1].find(d=>d.id==="new").parent_id,"d");
  assert.equal(v.get("settings-directory-name").value,"");
  assert.match(text(v.get("settings-directory-status")),/created/i);
});

test("root is immutable and directory names remain text; rename uses the exact directory",async()=>{
  const v=setup();await load(v);
  const rows=v.get("settings-directory-rows");assert.equal(all(rows).filter(n=>n.tag==="form").length,1);
  assert.equal(all(rows).filter(n=>["img","script"].includes(n.tag)).length,0);
  assert.match(text(rows),/root.*cannot be renamed/i);
  const form=renameForm(v),input=all(form).find(n=>n.tag==="input");input.value="Renamed";await form.fire("submit");
  assert.deepEqual(writes(v),[["rename","p","d","Renamed"]]);
  assert.match(text(v.get("settings-directory-rows")),/Renamed/);
  assert.ok(v.changed.length>=2);assert.ok(v.changed.every(p=>p==="p"));
  input.value="Detached";await form.fire("submit");assert.equal(writes(v).length,1);
});

test("invalid names and unknown parents cannot create directories",async()=>{
  for(const name of [" ","x/y","line\nfeed","\0","文".repeat(86)]) {
    const v=setup();await load(v);v.get("settings-directory-name").value=name;await create(v);assert.equal(writes(v).length,0);
  }
  const v=setup();await load(v);v.get("settings-directory-parent").value="another-project";
  v.get("settings-directory-name").value="Valid";await create(v);assert.equal(writes(v).length,0);
});

test("non-admin and stale project reads cannot install or mutate a directory snapshot",async()=>{
  for(const role of ["reader","editor",null]) {
    const v=setup();v.panel.setProject(role?"p":null,role);await load(v);await create(v);
    assert.deepEqual(v.calls,[]);assert.equal(v.get("settings-directory-form").hidden,true);
  }
  for(const [project,role] of [["q","admin"],[null,null],["p","reader"]]) {
    const late=deferred(),v=setup({listDirectories:()=>late.promise});const pending=load(v);
    v.panel.setProject(project,role);late.resolve({items:snapshot});await pending;
    assert.deepEqual(v.loaded,[]);assert.equal(v.get("settings-directory-rows").children.length,0);
  }
});

test("newest directory refresh wins and denied or cross-project snapshots clear editors",async()=>{
  const late=deferred();let reads=0;const v=setup({listDirectories:()=>++reads===1?late.promise:Promise.resolve({items:[snapshot[0]]})});
  const old=load(v);await load(v);late.resolve({items:snapshot});await old;assert.equal(renameForm(v),undefined);
  for(const read of [async()=>{throw new Error("denied");},async()=>({items:[{...snapshot[0],project_id:"q"}]})]) {
    v.panel.api.listDirectories=read;await load(v);assert.equal(v.get("settings-directory-rows").children.length,0);
    assert.equal(v.get("settings-directory-editor").disabled,true);
  }
});

test("directory mutation lock survives navigation and late settlement cannot refill another view",async()=>{
  const late=deferred();let count=0;const v=setup({createDirectory:()=>{count++;return late.promise;}});await load(v);
  v.get("settings-directory-name").value="New";const pending=create(v);await create(v);
  v.panel.setProject("q","admin");await load(v);await create(v);assert.equal(count,1);
  assert.equal(v.get("settings-directory-refresh").disabled,true);late.resolve({id:"new"});await pending;
  assert.equal(v.get("settings-directory-refresh").disabled,false);
  assert.equal(v.get("settings-directory-rows").children.length,0);assert.ok(v.changed.every(p=>p==="p"));
});

test("lost directory write response performs only read recovery and discards the old draft",async()=>{
  let count=0;const v=setup({createDirectory:async()=>{count++;throw new Error("response lost");}});await load(v);
  v.get("settings-directory-name").value="New";await create(v);
  assert.equal(count,1);assert.equal(v.calls.filter(c=>c[0]==="read").length,2);
  assert.equal(v.get("settings-directory-name").value,"");assert.match(text(v.get("settings-directory-status")),/unknown.*no automatic retry/i);
  await create(v);assert.equal(count,1);
});

test("directory HTTP adapters encode identity and use only the existing create and rename commands",async()=>{
  const api=await import("../../resources/js/api.js"),original=globalThis.fetch,calls=[];
  globalThis.fetch=async(...args)=>{calls.push(args);return new Response(JSON.stringify({data:{id:"d"}}));};
  try {
    await api.createDirectory("p/x","root","New");await api.renameDirectory("p/x","d/y","Renamed");
    assert.deepEqual(calls.map(([url])=>url),["/api/v1/projects/p%2Fx/directories","/api/v1/projects/p%2Fx/directories/d%2Fy"]);
    assert.deepEqual(calls.map(([,o])=>[o.method,o.credentials,JSON.parse(o.body)]),
      [["POST","same-origin",{parent_id:"root",name:"New"}],["PATCH","same-origin",{name:"Renamed"}]]);
  } finally {globalThis.fetch=original;}
});
