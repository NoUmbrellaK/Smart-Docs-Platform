import assert from "node:assert/strict";
import test from "node:test";

// Exercise the actual app bootstrap/event handlers, not a copy of its guards.
class Node {
  constructor(tag = "div") {
    Object.assign(this, { tag, children: [], listeners: {}, value: "", disabled: false });
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  dispatchEvent(event) { return this.listeners[event.type]?.(event); }
  removeAttribute() {}
  matches(selector) { return selector.split(", ").includes(this.tag); }
  querySelectorAll(selector) {
    return this.children.flatMap(n => [...(n.matches(selector) ? [n] : []), ...n.querySelectorAll(selector)]);
  }
}
const deferred = () => {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
};
const response = data => new Response(JSON.stringify({ data }), { status: 200 });
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve)); };

test("logout fences a delayed version load before it can enter the processing panel", async () => {
  const nodes = new Map(), versions = deferred(), logout = deferred(), taskList = deferred();
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  let documentReads = 0;
  globalThis.document = { getElementById: get, createElement: tag => new Node(tag),
    createTextNode: text => Object.assign(new Node(), { textContent: text }) };
  globalThis.fetch = async path => {
    if (path.endsWith("/me")) return response({ user: { id: "u", username: "editor" },
      projects: [{ id: "p", name: "project", role: "editor" }] });
    if (path.endsWith("/auth/logout")) return logout.promise;
    if (path.endsWith("/agent-tasks")) return taskList.promise;
    if (path.includes("/files?")) return response({ items: [{ id: "f", name: "file", directory_id: "d",
      current_version_id: "v", remote_ai_policy: "internal_only", deleted: false }], total: 1 });
    if (path.endsWith("/versions")) return versions.promise.then(value => value.clone());
    if (path.endsWith("/document")) {
      documentReads++;
      return response({ is_current: true, parse_job_id: "job", vector_job_id: null });
    }
    if (path.includes("/processing-jobs/")) return response({ id: "job", state: "failed", task_type: "parse_and_index" });
    if (path.includes("/uploads") || path.endsWith("/directories")) return response({ items: [], total: 0 });
    throw new Error(`Unexpected request: ${path}`);
  };
  try {
    await import("../../resources/js/app.js");
    await settle();
    assert.equal(get("global-status").textContent, "idle");
    const open = get("file-rows").querySelectorAll("button")[0];
    const selecting = open.listeners.click();
    const leaving = get("logout-button").listeners.click();
    taskList.resolve(response({items:[{id:"private-task",state:"pending",created_at:"2026-10-18"}],next_before:null}));
    versions.resolve(response({ items: [{ id: "v", media_type: "text/plain", version_number: 1 }] }));
    await selecting;
    await settle();
    assert.equal(documentReads, 0, "old file request must not restart processing after logout begins");
    assert.equal(get("processing-jobs").children.length, 0);
    assert.equal(get("agent-list").children.length, 0, "late task list must not repopulate after logout begins");
    assert.equal(get("agent-create").disabled, true);
    // A failed logout retains identity, but the old file request remains fenced.
    logout.resolve(new Response(JSON.stringify({ error: { code: "unavailable" } }), { status: 503 }));
    await leaving;
    assert.equal(get("signed-in").hidden, false);
    assert.equal(get("agent-refresh").disabled, false, "explicit refresh remains usable after failed logout");
    await open.listeners.click();
    await settle();
    assert.equal(documentReads, 1, "explicitly reopening after failed logout remains usable");
  } finally {
    delete globalThis.document;
    delete globalThis.fetch;
  }
});
