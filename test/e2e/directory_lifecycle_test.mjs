import assert from "node:assert/strict";
import test from "node:test";

class Node {
  constructor(tag="div") { Object.assign(this,{tag,children:[],listeners:{},value:"",disabled:false}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  addEventListener(type,fn) { this.listeners[type]=fn; }
  dispatchEvent(event) { return this.listeners[event.type]?.(event); }
  removeAttribute() {}
  matches(selector) { return selector.split(", ").includes(this.tag); }
  querySelectorAll(selector) { return this.children.flatMap(n=>[...(n.matches(selector)?[n]:[]),...n.querySelectorAll(selector)]); }
}
const response=data=>new Response(JSON.stringify({data}),{status:200});
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};

test("directory rename clears stale approval text and preserves file and directory selections",async()=>{
  const nodes=new Map(), get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
  const directories=[{id:"root",project_id:"p",parent_id:null,name:"Root"},{id:"d",project_id:"p",parent_id:"root",name:"Old"}];
  globalThis.document={getElementById:get,createElement:tag=>new Node(tag),createTextNode:text=>Object.assign(new Node(),{textContent:text})};
  globalThis.fetch=async(path,options={})=>{
    if(path.endsWith("/me"))return response({user:{id:"u",username:"admin"},projects:[{id:"p",name:"Project",role:"admin"}]});
    if(path.endsWith("/directories/d")) { assert.equal(options.method,"PATCH");directories[1].name=JSON.parse(options.body).name;return response(directories[1]); }
    if(path.endsWith("/directories"))return response({items:directories});
    if(path.endsWith("/archive-policy"))return response({revision:1,remote_ai_approved:true,targets:[{directory_id:"d",name:"Old"}]});
    if(path.includes("/files?"))return response({items:["f1","f2"].map(id=>({id,name:id,directory_id:"d",current_version_id:id+"v",deleted:false,remote_ai_policy:"internal_only"})),total:2});
    if(path.endsWith("/members")||path.includes("/uploads")||path.endsWith("/agent-tasks"))return response({items:[],total:0,next_before:null});
    throw new Error(`Unexpected request: ${path}`);
  };
  try {
    await import("../../resources/js/app.js");await settle();
    await get("settings-directory-refresh").listeners.click();
    await get("settings-archive-refresh").listeners.click();
    assert.match(get("settings-archive-status").textContent,/Currently approved/);
    for(const id of ["directory-filter","upload-directory","file-directory"])get(id).value="d";
    get("upload-file-target").value="f2";
    const form=get("settings-directory-rows").querySelectorAll("form")[0];
    form.querySelectorAll("input")[0].value="Renamed";
    await form.listeners.submit({preventDefault(){}});
    assert.equal(directories[1].name,"Renamed");
    assert.equal(get("settings-archive-rows").children.length,0);
    assert.equal(get("settings-archive-approval").checked,false);
    assert.doesNotMatch(get("settings-archive-status").textContent,/Currently approved/);
    assert.match(get("settings-archive-status").textContent,/refresh/i);
    for(const id of ["directory-filter","upload-directory","file-directory"]) {
      assert.equal(get(id).value,"d");
      assert.equal(get(id).children.find(n=>n.value==="d").textContent,"Renamed");
    }
    assert.equal(get("upload-file-target").value,"f2");
  } finally {delete globalThis.document;delete globalThis.fetch;}
});
