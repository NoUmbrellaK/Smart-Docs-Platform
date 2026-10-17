import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import test from "node:test";

class Node {
  constructor() { this.children = []; this.listeners = {}; this.disabled = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  fire() { return this.listeners.click(); }
}
globalThis.document = { createElement: () => new Node() };
const text = (node) => [node.textContent || "", ...node.children.map(text)].join(" ");
const buttons = (node) => [...(node.listeners.click ? [node] : []), ...node.children.flatMap(buttons)];
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve=yes; reject=no; }); return { promise, resolve, reject }; };
const doc = { file_id: "f", version_id: "v", is_current: true, parse_state: "failed",
  keyword_state: "pending", vector_state: "not_applicable", parse_job_id: "parse", vector_job_id: null,
  pages_without_text: [], page_count: 0, chunk_count: 0, char_count: 0, source_sha256: "a".repeat(64), parser_revision: "" };
const job = { id: "parse", project_id: "p", file_id: "f", version_id: "v",
  task_type: "parse_and_index", state: "failed", attempt_count: 1, error_code: "parse_timeout" };

async function setup(overrides = {}, canWrite = () => true) {
  const url = new URL("../../resources/js/processing.js", import.meta.url);
  assert.ok(existsSync(url), "processing task UI not implemented");
  const { ProcessingPanel } = await import(url.href);
  const nodes = new Map(), calls = [], mutations = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const api = { readDocument: async (...args) => { calls.push(["document", ...args]); return doc; },
    getProcessingJob: async (...args) => { calls.push(["get", ...args]); return job; },
    retryProcessingJob: async (...args) => { calls.push(["retry", ...args]); return { ...job, state: "pending" }; },
    cancelProcessingJob: async (...args) => { calls.push(["cancel", ...args]); return { ...job, state: "cancelled" }; }, ...overrides };
  const panel = new ProcessingPanel(get, api, e => e.message, canWrite, p => mutations.push(p));
  return { panel, get, calls, mutations };
}

test("reload obtains persisted job identity and independent states, not upload memory", async () => {
  const { panel, get, calls } = await setup();
  await panel.select("p", "f", "v");
  assert.deepEqual(calls, [["document", "p", "f", "v"], ["get", "p", "parse"]]);
  assert.match(text(get("processing-status")), /Parse: failed.*Keyword: pending.*Vector: not_applicable/);
  assert.match(text(get("processing-jobs")), /parse_timeout/);
  assert.match(text(get("processing-jobs")), /Attempts: 1/);
  assert.deepEqual(buttons(get("processing-jobs")).map(b => b.textContent), ["Retry"]);
});

test("retry is single flight and re-reads actual persisted state instead of promising completion", async () => {
  const pending = deferred(); let reads = 0;
  const { panel, get, mutations } = await setup({ retryProcessingJob: () => pending.promise,
    getProcessingJob: async () => ({ ...job, state: ++reads === 1 ? "failed" : "pending" }) });
  await panel.select("p", "f", "v");
  const retry = buttons(get("processing-jobs"))[0];
  const first = retry.fire();
  await retry.fire();
  assert.equal(mutations.length, 1);
  assert.equal(retry.disabled, true);
  pending.resolve({ ...job, state: "pending" });
  await first;
  assert.equal(reads, 2);
  assert.deepEqual(buttons(get("processing-jobs")).map(b => b.textContent), ["Cancel"]);
  assert.equal(get("processing-refresh").disabled, false);
});

test("vector failure remains independent and cancel targets the exact selected job", async () => {
  const { panel, get, calls } = await setup({ readDocument: async () => ({ ...doc,
    parse_state: "completed", keyword_state: "completed", vector_state: "running", vector_job_id: "vector" }),
    getProcessingJob: async (p, id) => ({ ...job, id, task_type: id === "parse" ? "parse_and_index" : "vectorize",
      state: id === "parse" ? "completed" : "running" }) });
  await panel.select("p", "f", "v");
  assert.match(text(get("processing-status")), /Keyword: completed/);
  await buttons(get("processing-jobs"))[0].fire();
  assert.ok(calls.some(c => JSON.stringify(c) === JSON.stringify(["cancel", "p", "vector"])));
});

test("reader and historical versions never offer writes; role changes fence already rendered buttons", async () => {
  let writable = true;
  const view = await setup({}, () => writable);
  await view.panel.select("p", "f", "v");
  const retry = buttons(view.get("processing-jobs"))[0];
  writable = false;
  await retry.fire();
  assert.equal(view.mutations.length, 0);
  await view.panel.refresh();
  assert.equal(buttons(view.get("processing-jobs")).length, 0);
  const old = await setup({ readDocument: async () => ({ ...doc, is_current: false }) });
  await old.panel.select("p", "f", "v");
  assert.match(text(old.get("processing-status")), /historical/i);
  assert.equal(buttons(old.get("processing-jobs")).length, 0);
});

test("project/file reset fences late status and old buttons", async () => {
  const late = deferred();
  const { panel, get, mutations } = await setup({ readDocument: () => late.promise });
  const pending = panel.select("p", "f", "v");
  panel.reset();
  late.resolve(doc);
  await pending;
  assert.equal(get("processing-jobs").children.length, 0);
  assert.equal(get("processing-refresh").disabled, true);
  await panel.select("p", "f", "v");
  const retry = buttons(get("processing-jobs"))[0];
  panel.reset();
  await retry.fire();
  assert.equal(mutations.length, 0);
});

test("late mutation clears search for original project but cannot overwrite new selection", async () => {
  const late = deferred();
  const { panel, get, mutations } = await setup({ retryProcessingJob: () => late.promise });
  await panel.select("p", "f", "v");
  const pending = buttons(get("processing-jobs"))[0].fire();
  panel.reset();
  late.resolve(job);
  await pending;
  assert.deepEqual(mutations, ["p", "p"]);
  assert.equal(get("processing-jobs").children.length, 0);
});

test("ambiguous mutation failure does not auto-retry or retain actionable stale buttons", async () => {
  const { panel, get, mutations } = await setup({ retryProcessingJob: async () => { throw new Error("response lost"); } });
  await panel.select("p", "f", "v");
  await buttons(get("processing-jobs"))[0].fire();
  assert.match(text(get("processing-status")), /response lost/);
  assert.match(text(get("processing-status")), /Refresh/);
  assert.equal(buttons(get("processing-jobs")).length, 0);
  assert.equal(get("processing-refresh").disabled, false);
  assert.deepEqual(mutations, ["p", "p"]);
});

test("null jobs are explicit and safe text remains text", async () => {
  const none = await setup({ readDocument: async () => ({ ...doc, parse_job_id: null }) });
  await none.panel.select("p", "f", "v");
  assert.match(text(none.get("processing-jobs")), /No .*task/i);
  assert.equal(none.calls.length, 0);
  const escaped = await setup({ getProcessingJob: async () => ({ ...job, error_code: "<img onerror=bad>" }) });
  await escaped.panel.select("p", "f", "v");
  assert.match(text(escaped.get("processing-jobs")), /<img onerror=bad>/);
});

test("task API uses same-origin exact job and empty POST bodies", async () => {
  const api = await import("../../resources/js/api.js");
  assert.equal(typeof api.getProcessingJob, "function");
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return new Response('{"data":{}}'); };
  try {
    await api.getProcessingJob("p", "job/1");
    await api.retryProcessingJob("p", "job/1");
    await api.cancelProcessingJob("p", "job/1");
    assert.deepEqual(calls.map(c => c[0]), ["/api/v1/projects/p/processing-jobs/job%2F1",
      "/api/v1/projects/p/processing-jobs/job%2F1/retry", "/api/v1/projects/p/processing-jobs/job%2F1/cancel"]);
    for (const [, options] of calls) assert.equal(options.credentials, "same-origin");
    for (const [, options] of calls.slice(1)) { assert.equal(options.method, "POST"); assert.equal(options.body, undefined); }
  } finally { globalThis.fetch = original; }
});
