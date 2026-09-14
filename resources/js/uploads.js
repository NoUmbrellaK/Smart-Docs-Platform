import {
  ApiError, cancelUpload, completeUpload, createUpload, getUpload,
  listUploads, parseApiResponse
} from "./api.js";
import { sha256 } from "./sha256.js";

export const MAX_PARALLEL_PARTS = 4;
const HASH_READ_BYTES = 2 * 1024 * 1024;
const RESTORABLE_STATES = new Set([
  "uploading", "interrupted", "assembling", "publishing"
]);

async function hashBlob(blob) {
  const digest = sha256.create();
  for (let offset = 0; offset < blob.size; offset += HASH_READ_BYTES) {
    digest.update(await blob.slice(offset, offset + HASH_READ_BYTES).arrayBuffer());
  }
  return digest.hex();
}

async function putPart(projectId, taskId, partNumber, blob, digest) {
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/uploads/` +
      `${encodeURIComponent(taskId)}/parts/${partNumber}`,
    {
      method: "PUT",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Chunk-SHA256": digest
      },
      body: blob
    }
  );
  return parseApiResponse(response);
}

const defaultDependencies = {
  cancelUpload,
  completeUpload,
  createUpload,
  getUpload,
  hashBlob,
  listUploads,
  putPart
};

function restoredState(detail) {
  return detail.state === "assembling" || detail.state === "publishing"
    ? "assembling"
    : "awaiting reselect";
}

function confirmedMap(parts) {
  return new Map((parts || []).map((part) => [part.part_number, part]));
}

function confirmedView(parts, size) {
  const map = confirmedMap(parts);
  const ordered = Array.from(map.values()).sort(
    (left, right) => left.part_number - right.part_number
  );
  return {
    map,
    parts: ordered,
    bytes: Math.min(size, ordered.reduce((total, part) => total + part.size, 0))
  };
}

function publicTask(detail, uiState) {
  // received_bytes is not trusted; keyed confirmed_parts are the progress source.
  const confirmed = confirmedView(detail.confirmed_parts, detail.size);
  return {
    taskId: detail.task_id,
    projectId: detail.project_id,
    mode: detail.mode,
    name: detail.name,
    fileId: detail.file_id,
    size: detail.size,
    sha256: detail.sha256,
    mediaType: detail.media_type,
    chunkSize: detail.chunk_size,
    partCount: detail.part_count,
    confirmedParts: confirmed.parts,
    confirmedBytes: confirmed.bytes,
    uiState,
    failure: detail.failure,
    versionId: detail.version_id,
    processingJobId: detail.processing_job_id,
    processingPhase: detail.processing_job_id ? "processing" : null
  };
}

class PutScheduler {
  constructor(limit) {
    this.limit = limit;
    this.active = 0;
    this.waiting = [];
  }

  acquire(valid) {
    if (!valid()) return Promise.resolve(null);
    return new Promise((resolve) => {
      this.waiting.push({ valid, resolve });
      this.drain();
    });
  }

  prune() {
    this.drain();
  }

  drain() {
    const validWaiting = [];
    for (const waiter of this.waiting) {
      if (waiter.valid()) validWaiting.push(waiter);
      else waiter.resolve(null);
    }
    this.waiting = validWaiting;
    while (this.active < this.limit && this.waiting.length > 0) {
      const waiter = this.waiting.shift();
      if (!waiter.valid()) {
        waiter.resolve(null);
        continue;
      }
      this.active += 1;
      let released = false;
      waiter.resolve(() => {
        if (released) return;
        released = true;
        this.active -= 1;
        this.drain();
      });
    }
  }
}

export class UploadController {
  constructor(onChange, dependencies = {}) {
    this.onChange = onChange;
    this.dependencies = { ...defaultDependencies, ...dependencies };
    this.projectId = null;
    this.selection = 0;
    this.runtimes = new Map();
    this.putScheduler = new PutScheduler(MAX_PARALLEL_PARTS);
  }

  snapshot() {
    return Array.from(this.runtimes.values(), ({ view }) => ({
      ...view,
      confirmedParts: [...view.confirmedParts]
    }));
  }

  emit(runtime) {
    this.onChange(this.snapshot(), runtime ? { ...runtime.view } : null);
  }

  makeRuntime(detail, uiState) {
    return {
      view: publicTask(detail, uiState),
      confirmed: confirmedMap(detail.confirmed_parts),
      file: null,
      paused: true,
      stop: false,
      generation: 0,
      selection: this.selection,
      transferPromise: null,
      cancelPromise: null,
      completePromise: null,
      completeGeneration: null
    };
  }

  sameGeneration(runtime, generation) {
    return runtime.generation === generation &&
      runtime.selection === this.selection &&
      this.runtimes.get(runtime.view.taskId) === runtime;
  }

  active(runtime, generation) {
    return this.sameGeneration(runtime, generation) &&
      !runtime.paused && !runtime.stop;
  }

  invalidate(runtime, { paused = true, stop = false } = {}) {
    runtime.generation += 1;
    runtime.paused = paused;
    runtime.stop = stop;
    runtime.file = null;
    runtime.transferPromise = null;
    runtime.cancelPromise = null;
    runtime.completePromise = null;
    runtime.completeGeneration = null;
    this.putScheduler.prune();
  }

  syncConfirmed(runtime) {
    const parts = Array.from(runtime.confirmed.values()).sort(
      (left, right) => left.part_number - right.part_number
    );
    runtime.view.confirmedParts = parts;
    runtime.view.confirmedBytes = Math.min(runtime.view.size, parts.reduce(
      (total, part) => total + part.size, 0
    ));
  }

  async refresh(projectId) {
    const selection = this.selection + 1;
    this.selection = selection;
    for (const runtime of this.runtimes.values()) this.invalidate(runtime);
    this.projectId = projectId;
    this.runtimes.clear();
    let page = 1;
    let received = 0;
    do {
      const result = await this.dependencies.listUploads(
        projectId, { page, pageSize: 100 }
      );
      if (selection !== this.selection) return this.snapshot();
      received += result.items.length;
      for (const item of result.items) {
        if (!RESTORABLE_STATES.has(item.state)) continue;
        const detail = await this.dependencies.getUpload(projectId, item.task_id);
        if (selection !== this.selection) return this.snapshot();
        this.runtimes.set(detail.task_id,
          this.makeRuntime(detail, restoredState(detail)));
      }
      page += 1;
      if (result.items.length === 0 || received >= result.total) break;
    } while (true);
    this.emit();
    return this.snapshot();
  }

  async start(file, command) {
    const projectId = this.projectId;
    const selection = this.selection;
    if (command.mode === "create_file") {
      if (!command.directory_id || !command.name) {
        throw new Error("A target directory and file name are required.");
      }
    } else if (command.mode === "create_version") {
      if (!command.file_id || !command.observed_current_version_id) {
        throw new Error("An existing file and its current version are required.");
      }
    } else {
      throw new Error("Upload mode is invalid.");
    }
    const preparing = {
      view: {
        taskId: null, projectId, name: file.name,
        size: file.size, confirmedBytes: 0,
        confirmedParts: [], uiState: "hashing", failure: null
      },
      file
    };
    this.emit(preparing);
    let detail;
    try {
      const wholeSha256 = await this.dependencies.hashBlob(file);
      if (selection !== this.selection) return preparing.view;
      const mediaType = (file.type || "application/octet-stream").split(";", 1)[0];
      detail = await this.dependencies.createUpload(projectId, {
        ...command,
        size: file.size,
        sha256: wholeSha256,
        media_type: mediaType
      });
      if (selection !== this.selection) return preparing.view;
    } catch (error) {
      preparing.file = null;
      preparing.view.uiState = "failed";
      preparing.view.failure = error.message;
      if (selection === this.selection) this.emit(preparing);
      throw error;
    }
    const runtime = this.makeRuntime(detail, "uploading");
    runtime.file = file;
    runtime.paused = false;
    runtime.selection = selection;
    this.runtimes.set(detail.task_id, runtime);
    this.emit(runtime);
    await this.beginUpload(runtime, file, detail);
    return runtime.view;
  }

  pause(taskId) {
    const runtime = this.requireRuntime(taskId);
    if (runtime.cancelPromise) return runtime.cancelPromise;
    this.invalidate(runtime);
    runtime.view.uiState = "interrupted";
    runtime.view.failure = null;
    this.emit(runtime);
    return runtime.view;
  }

  cancel(taskId) {
    const runtime = this.requireRuntime(taskId);
    if (runtime.cancelPromise) return runtime.cancelPromise;
    this.invalidate(runtime, { paused: true, stop: true });
    const generation = runtime.generation;
    runtime.view.uiState = "cancelling";
    runtime.view.failure = null;
    this.emit(runtime);
    let request;
    try {
      request = this.dependencies.cancelUpload(runtime.view.projectId, taskId);
    } catch (error) {
      request = Promise.reject(error);
    }
    let cancellation;
    cancellation = Promise.resolve(request).then(() => {
      if (!this.sameGeneration(runtime, generation)) return runtime.view;
      runtime.view.uiState = "cancelled";
      runtime.view.failure = null;
      this.emit(runtime);
      return runtime.view;
    }).catch((error) => {
      if (runtime.cancelPromise === cancellation) runtime.cancelPromise = null;
      if (this.sameGeneration(runtime, generation)) {
        runtime.stop = false;
        runtime.view.uiState = error.retryability ? "interrupted" : "failed";
        runtime.view.failure = error.message;
        this.emit(runtime);
      }
      throw error;
    });
    runtime.cancelPromise = cancellation;
    return cancellation;
  }

  resume(taskId, file) {
    const runtime = this.requireRuntime(taskId);
    if (runtime.cancelPromise) return runtime.cancelPromise;
    if (runtime.transferPromise) return runtime.transferPromise;
    if (runtime.view.uiState === "completed") return Promise.resolve(runtime.view);
    runtime.generation += 1;
    const generation = runtime.generation;
    runtime.paused = false;
    runtime.stop = false;
    runtime.file = file;
    runtime.view.uiState = "hashing";
    runtime.view.failure = null;
    this.emit(runtime);
    const transfer = this.resumeTransfer(runtime, generation, file);
    runtime.transferPromise = transfer;
    transfer.then(
      () => {
        if (runtime.transferPromise === transfer) runtime.transferPromise = null;
      },
      () => {
        if (runtime.transferPromise === transfer) runtime.transferPromise = null;
      }
    );
    return transfer;
  }

  async resumeTransfer(runtime, generation, file) {
    let readingFile = false;
    try {
      const detail = await this.dependencies.getUpload(
        runtime.view.projectId, runtime.view.taskId
      );
      if (!this.active(runtime, generation)) return runtime.view;
      runtime.view = publicTask(detail, "hashing");
      runtime.confirmed = confirmedMap(detail.confirmed_parts);
      this.syncConfirmed(runtime);
      this.emit(runtime);
      readingFile = true;
      if (file.size !== detail.size) {
        throw new ApiError(0, "file_mismatch",
          "The selected file size does not match this upload.", false, null);
      }
      const wholeSha256 = await this.dependencies.hashBlob(file);
      if (!this.active(runtime, generation)) return runtime.view;
      if (wholeSha256 !== detail.sha256) {
        throw new ApiError(0, "file_mismatch",
          "The selected file SHA-256 does not match this upload.", false, null);
      }
      runtime.view.uiState = "uploading";
      this.emit(runtime);
      await this.uploadMissing(runtime, generation, file, detail);
      return runtime.view;
    } catch (error) {
      if (!this.sameGeneration(runtime, generation)) return runtime.view;
      runtime.file = null;
      runtime.paused = true;
      runtime.view.uiState = readingFile
        ? "awaiting reselect"
        : (error.retryability ? "interrupted" : "failed");
      runtime.view.failure = error.message;
      this.emit(runtime);
      throw error;
    }
  }

  requireRuntime(taskId) {
    const runtime = this.runtimes.get(taskId);
    if (!runtime) throw new Error("Upload task is unavailable.");
    return runtime;
  }

  beginUpload(runtime, file, detail) {
    if (runtime.transferPromise) return runtime.transferPromise;
    runtime.generation += 1;
    const generation = runtime.generation;
    const transfer = this.uploadMissing(runtime, generation, file, detail);
    runtime.transferPromise = transfer;
    transfer.then(
      () => {
        if (runtime.transferPromise === transfer) runtime.transferPromise = null;
      },
      () => {
        if (runtime.transferPromise === transfer) runtime.transferPromise = null;
      }
    );
    return transfer;
  }

  async uploadMissing(runtime, generation, file, detail) {
    runtime.confirmed = confirmedMap(detail.confirmed_parts);
    this.syncConfirmed(runtime);
    const missing = [];
    for (let partNumber = 0; partNumber < detail.part_count; partNumber += 1) {
      if (!runtime.confirmed.has(partNumber)) missing.push(partNumber);
    }
    let cursor = 0;
    let firstError = null;
    const worker = async () => {
      while (this.active(runtime, generation) && cursor < missing.length) {
        const partNumber = missing[cursor];
        cursor += 1;
        const start = partNumber * detail.chunk_size;
        const blob = file.slice(start, start + detail.chunk_size);
        let digest;
        try {
          digest = await this.dependencies.hashBlob(blob);
        } catch (error) {
          if (this.active(runtime, generation)) {
            firstError = firstError || error;
            runtime.stop = true;
            this.putScheduler.prune();
          }
          return;
        }
        if (!this.active(runtime, generation)) return;
        const release = await this.putScheduler.acquire(
          () => this.active(runtime, generation)
        );
        if (!release) return;
        try {
          if (!this.active(runtime, generation)) return;
          const part = await this.dependencies.putPart(
            runtime.view.projectId, detail.task_id, partNumber, blob, digest
          );
          if (!this.active(runtime, generation)) return;
          runtime.confirmed.set(part.part_number, part);
          this.syncConfirmed(runtime);
          this.emit(runtime);
        } catch (error) {
          if (this.active(runtime, generation)) {
            firstError = firstError || error;
            runtime.stop = true;
            this.putScheduler.prune();
          }
          return;
        } finally {
          release();
        }
      }
    };
    await Promise.all(Array.from({ length: MAX_PARALLEL_PARTS }, worker));
    if (!this.sameGeneration(runtime, generation)) return runtime.view;
    if (firstError) {
      runtime.file = null;
      runtime.paused = true;
      runtime.view.uiState = firstError.retryability ? "interrupted" : "failed";
      runtime.view.failure = firstError.message;
      this.emit(runtime);
      return runtime.view;
    }
    if (runtime.paused || runtime.stop) return runtime.view;
    runtime.file = null;
    await this.completeOnce(runtime, generation);
    return runtime.view;
  }

  completeOnce(runtime, generation) {
    if (!this.active(runtime, generation)) return Promise.resolve(null);
    if (runtime.completePromise && runtime.completeGeneration === generation) {
      return runtime.completePromise;
    }
    runtime.view.uiState = "assembling";
    this.emit(runtime);
    runtime.completeGeneration = generation;
    const completing = this.dependencies.completeUpload(
      runtime.view.projectId, runtime.view.taskId
    ).then((result) => {
      if (!this.active(runtime, generation)) return result;
      runtime.view.fileId = result.file_id;
      runtime.view.versionId = result.version_id;
      runtime.view.processingJobId = result.processing_job_id;
      runtime.view.processingPhase = "processing";
      runtime.view.uiState = "completed";
      runtime.file = null;
      this.emit(runtime);
      return result;
    }).catch((error) => {
      if (!this.sameGeneration(runtime, generation)) return null;
      runtime.completePromise = null;
      runtime.completeGeneration = null;
      runtime.paused = true;
      runtime.view.uiState = error.retryability ? "interrupted" : "failed";
      runtime.view.failure = error.message;
      this.emit(runtime);
      throw error;
    });
    runtime.completePromise = completing;
    return completing;
  }
}
