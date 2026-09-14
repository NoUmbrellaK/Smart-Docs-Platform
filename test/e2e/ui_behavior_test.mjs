#!/usr/bin/env node

import assert from "node:assert/strict";
import test from "node:test";

const ROOT = new URL("../../", import.meta.url);
const moduleUrl = (name) => new URL(`resources/js/${name}`, ROOT).href;
const delay = (milliseconds = 0) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function fileOf(size, name = "document.bin") {
  return new File([new Uint8Array(size)], name, {
    type: "application/octet-stream"
  });
}

function uploadDetail(taskId, overrides = {}) {
  const size = overrides.size ?? 4;
  const chunkSize = overrides.chunk_size ?? 1;
  return {
    task_id: taskId,
    project_id: "project-a",
    mode: "create_file",
    name: `${taskId}.bin`,
    file_id: null,
    size,
    sha256: size === 0 ?
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" :
      "whole",
    media_type: "application/octet-stream",
    chunk_size: chunkSize,
    part_count: size === 0 ? 0 : Math.ceil(size / chunkSize),
    confirmed_parts: [],
    received_bytes: 0,
    state: "uploading",
    failure: null,
    version_id: null,
    processing_job_id: null,
    ...overrides
  };
}

function dependencies(details, overrides = {}) {
  const byId = new Map(details.map((detail) => [detail.task_id, detail]));
  return {
    listUploads: async () => ({
      items: details.map((detail) => ({
        task_id: detail.task_id,
        state: detail.state
      })),
      total: details.length
    }),
    getUpload: async (_projectId, taskId) => byId.get(taskId),
    createUpload: async () => { throw new Error("unexpected create"); },
    cancelUpload: async () => null,
    completeUpload: async (_projectId, taskId) => ({
      file_id: `file-${taskId}`,
      version_id: `version-${taskId}`,
      processing_job_id: `job-${taskId}`
    }),
    hashBlob: async (blob) => blob.size === 0 ?
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" :
      (blob.size === byId.values().next().value.size ? "whole" : "part"),
    putPart: async (_projectId, _taskId, partNumber, blob, digest) => ({
      part_number: partNumber,
      size: blob.size,
      sha256: digest
    }),
    ...overrides
  };
}

async function restoredController(details, overrides = {}, onChange = () => {}) {
  const { UploadController } = await import(moduleUrl("uploads.js"));
  const controller = new UploadController(onChange,
    dependencies(details, overrides));
  await controller.refresh("project-a");
  return controller;
}

test("SHA-256 wrapper handles zero, abc, and split ArrayBuffer updates", async () => {
  const { sha256 } = await import(moduleUrl("sha256.js"));
  const encoder = new TextEncoder();
  const empty = sha256.create();
  assert.equal(empty.hex(),
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  const abc = sha256.create().update(encoder.encode("abc").buffer);
  assert.equal(abc.hex(),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  const split = sha256.create();
  split.update(encoder.encode("a").buffer);
  split.update(encoder.encode("bc").buffer);
  assert.equal(split.hex(), abc.hex());
});

test("API response parser accepts 204 and maps the unified error", async () => {
  const { ApiError, parseApiResponse } = await import(moduleUrl("api.js"));
  assert.equal(await parseApiResponse(new Response(null, { status: 204 })), null);
  const response = new Response(JSON.stringify({
    error: { code: "busy", message: "Try later", retryable: true },
    request_id: "request-7"
  }), { status: 503, headers: { "Content-Type": "application/json" } });
  await assert.rejects(parseApiResponse(response), (error) => {
    assert.ok(error instanceof ApiError);
    assert.deepEqual(
      [error.status, error.code, error.retryability, error.requestId],
      [503, "busy", true, "request-7"]
    );
    return true;
  });
});

test("logout API rejects an unexpected successful status", async () => {
  const { ApiError, logout } = await import(moduleUrl("api.js"));
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({ data: { still_signed_in: true } }),
    { status: 200, headers: { "Content-Type": "application/json" } }
  );
  try {
    await assert.rejects(logout(), (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.code, "invalid_response");
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("all upload tasks share four actual PUT slots", async () => {
  const details = [uploadDetail("task-1"), uploadDetail("task-2")];
  let active = 0;
  let peak = 0;
  const controller = await restoredController(details, {
    hashBlob: async (blob) => blob.size === 4 ? "whole" : "part",
    putPart: async (_projectId, _taskId, partNumber, blob) => {
      active += 1;
      peak = Math.max(peak, active);
      await delay(5);
      active -= 1;
      return { part_number: partNumber, size: blob.size };
    }
  });
  await Promise.all([
    controller.resume("task-1", fileOf(4)),
    controller.resume("task-2", fileOf(4))
  ]);
  assert.equal(peak, 4);
});

test("invalidated slot waiters do not PUT or leak capacity", async () => {
  const details = [uploadDetail("holder"), uploadDetail("waiter")];
  const holderPuts = [];
  let waiterPuts = 0;
  const controller = await restoredController(details, {
    hashBlob: async () => "whole",
    putPart: async (_projectId, taskId, partNumber, blob) => {
      if (taskId === "holder") {
        const gate = deferred();
        holderPuts.push({ gate, partNumber, size: blob.size });
        return gate.promise;
      }
      waiterPuts += 1;
      return { part_number: partNumber, size: blob.size };
    }
  });
  const holder = controller.resume("holder", fileOf(4));
  while (holderPuts.length < 4) await delay();
  const staleWaiter = controller.resume("waiter", fileOf(4));
  await delay();
  controller.pause("waiter");
  for (const pending of holderPuts) {
    pending.gate.resolve({
      part_number: pending.partNumber,
      size: pending.size
    });
  }
  await Promise.all([holder, staleWaiter]);
  assert.equal(waiterPuts, 0);
  await controller.resume("waiter", fileOf(4));
  assert.equal(waiterPuts, 4);
});

test("duplicate resume is single-flight and completes only once", async () => {
  const detail = uploadDetail("task-single");
  const hashing = deferred();
  let hashes = 0;
  let puts = 0;
  let completes = 0;
  const controller = await restoredController([detail], {
    hashBlob: async (blob) => {
      hashes += 1;
      if (hashes === 1) return hashing.promise;
      return blob.size === 4 ? "whole" : "part";
    },
    putPart: async (_projectId, _taskId, partNumber, blob) => {
      puts += 1;
      return { part_number: partNumber, size: blob.size };
    },
    completeUpload: async () => {
      completes += 1;
      return { file_id: "file", version_id: "version", processing_job_id: "job" };
    }
  });
  const first = controller.resume("task-single", fileOf(4));
  const second = controller.resume("task-single", fileOf(4));
  assert.equal(first, second);
  hashing.resolve("whole");
  await Promise.all([first, second]);
  assert.equal(puts, 4);
  assert.equal(completes, 1);
});

test("pause after part hashing starts schedules no later PUT", async () => {
  const detail = uploadDetail("task-pause");
  const partHash = deferred();
  let hashCalls = 0;
  let puts = 0;
  const controller = await restoredController([detail], {
    hashBlob: async () => {
      hashCalls += 1;
      if (hashCalls === 1) return "whole";
      return partHash.promise;
    },
    putPart: async () => { puts += 1; }
  });
  const transfer = controller.resume("task-pause", fileOf(4));
  while (hashCalls < 2) await delay();
  controller.pause("task-pause");
  partHash.resolve("part");
  await transfer;
  assert.equal(puts, 0);
  assert.equal(controller.snapshot()[0].uiState, "interrupted");
});

test("project refresh during hashing invalidates the old upload", async () => {
  const detail = uploadDetail("task-project");
  const partHash = deferred();
  let hashCalls = 0;
  let puts = 0;
  const controller = await restoredController([detail], {
    listUploads: async (projectId) => projectId === "project-a"
      ? { items: [{ task_id: detail.task_id, state: detail.state }], total: 1 }
      : { items: [], total: 0 },
    hashBlob: async () => {
      hashCalls += 1;
      if (hashCalls === 1) return "whole";
      return partHash.promise;
    },
    putPart: async () => { puts += 1; }
  });
  const oldTransfer = controller.resume("task-project", fileOf(4));
  while (hashCalls < 2) await delay();
  await controller.refresh("project-b");
  partHash.resolve("part");
  await oldTransfer;
  assert.equal(puts, 0);
  assert.deepEqual(controller.snapshot(), []);
});

test("cancel remains final when in-flight PUTs finish or fail late", async () => {
  const detail = uploadDetail("task-cancel");
  const pending = [];
  const states = [];
  const controller = await restoredController([detail], {
    hashBlob: async (blob) => blob.size === 4 ? "whole" : "part",
    putPart: async () => {
      const gate = deferred();
      pending.push(gate);
      return gate.promise;
    }
  }, (snapshot) => states.push(snapshot.map((task) => task.uiState)));
  const transfer = controller.resume("task-cancel", fileOf(4));
  while (pending.length === 0) await delay();
  await controller.cancel("task-cancel");
  pending.forEach((gate) => gate.reject(new Error("late PUT failure")));
  await transfer;
  assert.equal(controller.snapshot()[0].uiState, "cancelled");
  assert.equal(states.at(-1)[0], "cancelled");
});

test("pending cancel is single-flight and blocks pause and resume", async () => {
  const detail = uploadDetail("task-cancelling");
  const cancelGate = deferred();
  const pendingPuts = [];
  let cancelCalls = 0;
  let putCalls = 0;
  const controller = await restoredController([detail], {
    hashBlob: async (blob) => blob.size === 4 ? "whole" : "part",
    putPart: async (_projectId, _taskId, partNumber, blob) => {
      putCalls += 1;
      const gate = deferred();
      pendingPuts.push({ gate, partNumber, size: blob.size });
      return gate.promise;
    },
    cancelUpload: async () => {
      cancelCalls += 1;
      return cancelGate.promise;
    }
  });
  const transfer = controller.resume("task-cancelling", fileOf(4));
  while (pendingPuts.length === 0) await delay();
  const firstCancel = controller.cancel("task-cancelling");
  assert.equal(controller.snapshot()[0].uiState, "cancelling");
  const pauseDuringCancel = controller.pause("task-cancelling");
  const resumeDuringCancel = controller.resume("task-cancelling", fileOf(4));
  const duplicateCancel = controller.cancel("task-cancelling");
  assert.equal(pauseDuringCancel, firstCancel);
  assert.equal(resumeDuringCancel, firstCancel);
  assert.equal(duplicateCancel, firstCancel);
  assert.equal(cancelCalls, 1);
  const callsAtCancel = putCalls;
  cancelGate.resolve(null);
  await Promise.all([
    firstCancel, pauseDuringCancel, resumeDuringCancel, duplicateCancel
  ]);
  assert.equal(controller.snapshot()[0].uiState, "cancelled");
  for (const pending of pendingPuts) {
    pending.gate.reject(new Error("late PUT failure"));
  }
  await transfer;
  assert.equal(putCalls, callsAtCancel);
  assert.equal(controller.snapshot()[0].uiState, "cancelled");
});

test("failed cancel clears pending state and can be retried", async () => {
  const { ApiError } = await import(moduleUrl("api.js"));
  const detail = uploadDetail("task-cancel-retry");
  let cancelCalls = 0;
  const controller = await restoredController([detail], {
    cancelUpload: async () => {
      cancelCalls += 1;
      if (cancelCalls === 1) {
        throw new ApiError(503, "busy", "Try cancellation again", true, "r9");
      }
      return null;
    }
  });
  await assert.rejects(controller.cancel("task-cancel-retry"),
                       /Try cancellation again/);
  assert.equal(controller.snapshot()[0].uiState, "interrupted");
  await controller.cancel("task-cancel-retry");
  assert.equal(cancelCalls, 2);
  assert.equal(controller.snapshot()[0].uiState, "cancelled");
});

test("confirmed parts are keyed and confirmed bytes never exceed size", async () => {
  const duplicate = uploadDetail("task-dedupe", {
    size: 2,
    part_count: 2,
    confirmed_parts: [
      { part_number: 0, size: 1 },
      { part_number: 0, size: 9 }
    ],
    received_bytes: 10
  });
  const observed = [];
  const controller = await restoredController([duplicate], {
    hashBlob: async (blob) => blob.size === 2 ? "whole" : "part"
  }, (snapshot) => observed.push(snapshot));
  assert.equal(controller.snapshot()[0].confirmedParts.length, 1);
  assert.ok(controller.snapshot()[0].confirmedBytes <= 2);
  await controller.resume("task-dedupe", fileOf(2));
  assert.ok(observed.flat().every((task) => task.confirmedBytes <= task.size));
});

test("resume read failure clears the file and returns to actionable state", async () => {
  const detail = uploadDetail("task-read");
  const controller = await restoredController([detail], {
    hashBlob: async () => { throw new Error("local file read failed"); }
  });
  await assert.rejects(controller.resume("task-read", fileOf(4)),
                       /local file read failed/);
  const task = controller.snapshot()[0];
  assert.equal(task.uiState, "awaiting reselect");
  assert.match(task.failure, /local file read failed/);
});

test("zero-byte upload resumes and calls complete exactly once", async () => {
  const detail = uploadDetail("task-empty", { size: 0, part_count: 0 });
  let completes = 0;
  const controller = await restoredController([detail], {
    completeUpload: async () => {
      completes += 1;
      return { file_id: "empty", version_id: "v1", processing_job_id: "job" };
    }
  });
  const first = controller.resume("task-empty", fileOf(0, "empty.bin"));
  const second = controller.resume("task-empty", fileOf(0, "empty.bin"));
  assert.equal(first, second);
  await first;
  assert.equal(completes, 1);
  assert.equal(controller.snapshot()[0].uiState, "completed");
});

test("project generation drops A after B and project actions retain scope", async () => {
  const { ProjectGeneration } = await import(moduleUrl("app.js"));
  const gate = new ProjectGeneration();
  const a = gate.begin("project-a");
  const aResult = deferred();
  const rendered = [];
  const lateA = aResult.promise.then((value) => gate.commit(a, () => rendered.push(value)));
  const b = gate.begin("project-b");
  gate.commit(b, () => rendered.push("B data"));
  aResult.resolve("A data");
  await lateA;
  assert.deepEqual(rendered, ["B data"]);

  const mutations = [];
  const staleMemberAction = gate.action(a,
    (projectId, userId) => mutations.push([projectId, userId]));
  const currentMemberAction = gate.action(b,
    (projectId, userId) => mutations.push([projectId, userId]));
  assert.equal(await staleMemberAction("A-user"), false);
  assert.equal(await currentMemberAction("B-user"), true);
  assert.deepEqual(mutations, [["project-b", "B-user"]]);
});

test("logout only clears UI after 204-equivalent success or explicit 401", async () => {
  const { ApiError } = await import(moduleUrl("api.js"));
  const { performLogout } = await import(moduleUrl("app.js"));
  let signedOut = 0;
  const errors = [];
  assert.equal(await performLogout(async () => null,
    () => { signedOut += 1; }, (message) => errors.push(message)), true);
  assert.equal(await performLogout(async () => {
    throw new ApiError(401, "unauthenticated", "No session", false, "r1");
  }, () => { signedOut += 1; }, (message) => errors.push(message)), true);
  assert.equal(await performLogout(async () => {
    throw new ApiError(503, "busy", "Try again", true, "r2");
  }, () => { signedOut += 1; }, (message) => errors.push(message)), false);
  assert.equal(signedOut, 2);
  assert.match(errors.at(-1), /Try again/);
});

test("permission state hides writes synchronously on admin to reader switch", async () => {
  const { writeVisibility } = await import(moduleUrl("app.js"));
  assert.deepEqual(writeVisibility("admin", true, { deleted: false }), {
    upload: true, update: true, delete: true, restore: false,
    remoteAi: true, members: true
  });
  assert.deepEqual(writeVisibility("reader", false, null), {
    upload: false, update: false, delete: false, restore: false,
    remoteAi: false, members: false
  });
  assert.deepEqual(writeVisibility("editor", true, { deleted: true }), {
    upload: true, update: false, delete: false, restore: true,
    remoteAi: false, members: false
  });
});

test("confirmed self-demotion synchronizes identity, project, and visibility", async () => {
  const { synchronizeSelfRole } = await import(moduleUrl("app.js"));
  const identity = {
    user: { id: "self", username: "owner" },
    projects: [
      { id: "project-a", name: "A", role: "admin" },
      { id: "project-b", name: "B", role: "reader" }
    ]
  };
  const project = identity.projects[0];
  const selectedFile = { id: "file-a", deleted: false };
  const editor = synchronizeSelfRole(
    identity, project, "self", { role: "editor" }, true, selectedFile
  );
  assert.equal(editor.changed, true);
  assert.equal(editor.identity.projects[0].role, "editor");
  assert.equal(editor.project.role, "editor");
  assert.deepEqual(editor.visibility, {
    upload: true, update: true, delete: true, restore: false,
    remoteAi: false, members: false
  });
  assert.equal(identity.projects[0].role, "admin");

  const reader = synchronizeSelfRole(
    identity, project, "self", { role: "reader" }, true, selectedFile
  );
  assert.equal(reader.identity.projects[0].role, "reader");
  assert.equal(reader.project.role, "reader");
  assert.deepEqual(reader.visibility, {
    upload: false, update: false, delete: false, restore: false,
    remoteAi: false, members: false
  });

  const other = synchronizeSelfRole(
    identity, project, "another-user", { role: "reader" }, true, selectedFile
  );
  assert.equal(other.changed, false);
  assert.equal(other.project.role, "admin");
});
