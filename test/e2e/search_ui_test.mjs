import assert from "node:assert/strict";
import test from "node:test";
import { SearchPanel } from "../../resources/js/search.js";

class Node {
  constructor() { this.children = []; this.listeners = {}; this.value = ""; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  fire(type) { return this.listeners[type]({ preventDefault() {} }); }
}
globalThis.document = { createElement: () => new Node() };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const item = { name: "<script>not HTML</script>", file_id: "f", version_id: "v",
  media_type: "application/pdf", parse_state: "completed", keyword_state: "completed",
  vector_state: "pending", availability_state: "available",
  chunk: { chunk_id: "c", text: "<img onerror=bad>", page: 2, line_start: 1, line_end: 3 } };
const result = (overrides = {}) => ({ items: [item], page: 1, next_page: 2,
  has_more: true, truncated: true, retrieval: "keyword_only",
  coverage: { current_files: 5, keyword_ready_files: 3, unsearched_files: 2 },
  warnings: ["live_pagination", "vector_search_unavailable"], ...overrides });
function setup(overrides = {}) {
  const nodes = new Map();
  const get = (id) => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const calls = [];
  const api = { searchDocuments: async (p, q) => { calls.push([p, q]); return result(); },
    readDocument: async () => ({ ...item, is_current: false, pages_without_text: [1] }),
    versionContentUrl: (p, f, v) => `/api/${p}/${f}/${v}/content`, ...overrides };
  get("document-search-mode").value = "keyword";
  get("document-search-query").value = "handover";
  const panel = new SearchPanel(get, api, (error) => error.message);
  panel.setProject("p", []);
  return { panel, get, calls };
}
const text = (node) => [node.textContent || "", ...node.children.map(text)].join(" ");

test("search submits bounded exact filters, paginates and shows truthful coverage", async () => {
  const { get, calls } = setup();
  get("document-search-directory").value = "d";
  get("document-search-media").value = "application/pdf";
  await get("document-search-form").fire("submit");
  assert.deepEqual(calls[0], ["p", { mode: "keyword", query: "handover", directory_id: "d",
    media_type: "application/pdf", page: 1, page_size: 10 }]);
  assert.match(text(get("document-search-status")), /2 unindexed/);
  assert.match(text(get("document-search-status")), /not a match count/);
  assert.match(text(get("document-search-status")), /keyword_only/);
  assert.match(text(get("document-search-results")), /<script>not HTML<\/script>/);
  assert.match(text(get("document-search-results")), /<img onerror=bad>/);
  await get("document-search-next").fire("click");
  assert.equal(calls[1][1].page, 2);
});

test("project changes and changed filters fence late responses and old source buttons", async () => {
  const delayed = deferred();
  const { get, panel } = setup({ searchDocuments: () => delayed.promise });
  const pending = get("document-search-form").fire("submit");
  panel.setProject("other", []);
  delayed.resolve(result());
  await pending;
  assert.equal(get("document-search-results").children.length, 0);
  await get("document-search-form").fire("submit");
  const button = get("document-search-results").children[0].children.at(-1);
  get("document-search-query").fire("input");
  await button.fire("click");
  assert.equal(get("document-search-source").children.length, 0);
  assert.equal(get("document-search-next").disabled, true);
});

test("exact source is reauthorized; old version, PDF page and missing text are explicit", async () => {
  const calls = [];
  const { get } = setup({ readDocument: async (...args) => {
    calls.push(args); return { ...item, is_current: false, pages_without_text: [1] };
  }});
  await get("document-search-form").fire("submit");
  await get("document-search-results").children[0].children.at(-1).fire("click");
  assert.deepEqual(calls, [["p", "f", "v", "c"]]);
  const source = get("document-search-source");
  assert.match(text(get("document-source-status")), /historical version/);
  assert.match(text(source), /Pages without extracted text: 1/);
  assert.equal(source.children.at(-1).href, "/api/p/f/v/content#page=2");
});

test("failed source reads clear old content; logout fences a pending source", async () => {
  let read = 0;
  const delayed = deferred();
  const { get, panel } = setup({ readDocument: () => {
    if (++read === 1) return Promise.reject(new Error("Forbidden"));
    return delayed.promise;
  }});
  await get("document-search-form").fire("submit");
  const button = get("document-search-results").children[0].children.at(-1);
  get("document-search-source").append(new Node());
  await button.fire("click");
  assert.equal(get("document-search-source").children.length, 0);
  assert.match(text(get("document-source-status")), /Forbidden/);
  const pending = button.fire("click");
  panel.setProject(null, []);
  delayed.resolve({ ...item, is_current: true, pages_without_text: [] });
  await pending;
  assert.equal(get("document-search-source").children.length, 0);
});

test("UTF-8 byte limits, blank keywords, final-page limits and retry errors", async () => {
  const { get, calls } = setup({ searchDocuments: async () => result({ next_page: null,
    page: 1000, warnings: ["page_limit_reached"] }) });
  get("document-search-query").value = "文".repeat(86);
  await get("document-search-form").fire("submit");
  assert.match(text(get("document-search-status")), /256 UTF-8 bytes/);
  get("document-search-query").value = "   ";
  await get("document-search-form").fire("submit");
  assert.match(text(get("document-search-status")), /Enter a keyword/);
  assert.equal(calls.length, 0);
  get("document-search-query").value = "handover";
  await get("document-search-form").fire("submit");
  assert.equal(get("document-search-next").disabled, true);
  assert.match(text(get("document-search-status")), /page_limit_reached/);
  const failure = setup({ searchDocuments: async () => { throw new Error("Unavailable"); } });
  await failure.get("document-search-form").fire("submit");
  assert.equal(failure.get("document-search-results").children.length, 0);
  assert.match(text(failure.get("document-search-status")), /Unavailable/);
});

test("newer searches and source selections win over late successes and errors", async () => {
  const oldSearch = deferred(), oldSource = deferred();
  let searches = 0, reads = 0;
  const { get } = setup({ searchDocuments: () => ++searches === 1 ? oldSearch.promise :
    Promise.resolve(result({ items: [item, { ...item, file_id: "second" }] })),
    readDocument: () => ++reads === 1 ? oldSource.promise :
      Promise.resolve({ ...item, file_id: "second", is_current: true, pages_without_text: [] }) });
  const old = get("document-search-form").fire("submit");
  await get("document-search-form").fire("submit");
  oldSearch.reject(new Error("stale failure"));
  await old;
  assert.doesNotMatch(text(get("document-search-status")), /stale failure/);
  const cards = get("document-search-results").children;
  const pending = cards[0].children.at(-1).fire("click");
  await cards[1].children.at(-1).fire("click");
  oldSource.resolve({ ...item, is_current: false, pages_without_text: [] });
  await pending;
  assert.match(text(get("document-search-source")), /File second/);
  assert.doesNotMatch(text(get("document-source-status")), /historical/);
});

test("API sends same-origin search POST and exact encoded version/chunk GETs", async () => {
  const api = await import("../../resources/js/api.js");
  const requests = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    requests.push([url, options]);
    return new Response(JSON.stringify({ data: {} }), { status: 200 });
  };
  try {
    await api.searchDocuments("p", { mode: "metadata", query: "", page: 1 });
    await api.readDocument("p", "f", "v", "c/1");
    await api.readDocument("p", "f", "old");
    assert.equal(requests[0][0], "/api/v1/projects/p/search");
    assert.equal(requests[0][1].method, "POST");
    assert.equal(requests[0][1].credentials, "same-origin");
    assert.equal(JSON.parse(requests[0][1].body).query, "");
    assert.equal(requests[1][0], "/api/v1/projects/p/files/f/versions/v/chunks/c%2F1");
    assert.equal(requests[2][0], "/api/v1/projects/p/files/f/versions/old/document");
  } finally { globalThis.fetch = original; }
});
