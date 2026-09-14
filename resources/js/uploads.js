import {
  ApiError, cancelUpload, completeUpload, createUpload, getUpload,
  listUploads, parseApiResponse
} from "/js/api.js";
import { sha256 } from "/js/sha256.js";

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

function restoredState(detail) {
  return detail.state === "assembling" || detail.state === "publishing"
    ? "assembling"
    : "awaiting reselect";
}

function publicTask(detail, uiState) {
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
    confirmedParts: detail.confirmed_parts || [],
    confirmedBytes: detail.received_bytes,
    uiState,
    failure: detail.failure,
    versionId: detail.version_id,
    processingJobId: detail.processing_job_id,
    processingPhase: detail.processing_job_id ? "processing" : null
  };
}

export class UploadController {
  constructor(onChange) {
    this.onChange = onChange;
    this.projectId = null;
    this.selection = 0;
    this.runtimes = new Map();
  }

  snapshot() {
    return Array.from(this.runtimes.values(), ({ view }) => ({ ...view }));
  }

  emit(runtime) {
    this.onChange(this.snapshot(), runtime ? { ...runtime.view } : null);
  }

  async refresh(projectId) {
    const selection = this.selection + 1;
    this.selection = selection;
    for (const runtime of this.runtimes.values()) {
      runtime.paused = true;
      runtime.file = null;
    }
    this.projectId = projectId;
    this.runtimes.clear();
    let page = 1;
    let received = 0;
    do {
      const result = await listUploads(projectId, { page, pageSize: 100 });
      if (selection !== this.selection) return this.snapshot();
      received += result.items.length;
      for (const item of result.items) {
        if (!RESTORABLE_STATES.has(item.state)) continue;
        const detail = await getUpload(projectId, item.task_id);
        if (selection !== this.selection) return this.snapshot();
        this.runtimes.set(detail.task_id, {
          view: publicTask(detail, restoredState(detail)),
          file: null,
          paused: true,
          stop: false,
          completePromise: null
        });
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
    const runtime = {
      view: {
        taskId: null, projectId, name: file.name,
        size: file.size, confirmedBytes: 0,
        confirmedParts: [], uiState: "hashing", failure: null
      },
      file, paused: false, stop: false, completePromise: null
    };
    this.emit(runtime);
    let detail;
    try {
      const wholeSha256 = await hashBlob(file);
      if (selection !== this.selection) {
        throw new Error("Project changed before the upload task was created.");
      }
      const mediaType = (file.type || "application/octet-stream").split(";", 1)[0];
      detail = await createUpload(projectId, {
        ...command,
        size: file.size,
        sha256: wholeSha256,
        media_type: mediaType
      });
      if (selection !== this.selection) {
        throw new Error("Project changed; reselect this file when returning to the project.");
      }
    } catch (error) {
      runtime.file = null;
      runtime.view.uiState = "failed";
      runtime.view.failure = error.message;
      if (selection === this.selection) this.emit(runtime);
      throw error;
    }
    runtime.view = publicTask(detail, "uploading");
    runtime.file = file;
    this.runtimes.set(detail.task_id, runtime);
    this.emit(runtime);
    await this.uploadMissing(runtime);
    return runtime.view;
  }

  pause(taskId) {
    const runtime = this.requireRuntime(taskId);
    runtime.paused = true;
    runtime.file = null;
    runtime.view.uiState = "interrupted";
    this.emit(runtime);
  }

  async cancel(taskId) {
    const runtime = this.requireRuntime(taskId);
    runtime.paused = true;
    runtime.stop = true;
    runtime.file = null;
    await cancelUpload(runtime.view.projectId, taskId);
    runtime.view.uiState = "cancelled";
    this.emit(runtime);
  }

  async resume(taskId, file) {
    const runtime = this.requireRuntime(taskId);
    const detail = await getUpload(runtime.view.projectId, taskId);
    runtime.view = publicTask(detail, "hashing");
    this.emit(runtime);
    if (file.size !== detail.size) {
      runtime.view.uiState = "awaiting reselect";
      runtime.view.failure = "The selected file size does not match this upload.";
      this.emit(runtime);
      throw new ApiError(0, "file_mismatch",
                         "The selected file size does not match this upload.",
                         false, null);
    }
    const wholeSha256 = await hashBlob(file);
    if (wholeSha256 !== detail.sha256) {
      runtime.view.uiState = "awaiting reselect";
      runtime.view.failure = "The selected file SHA-256 does not match this upload.";
      this.emit(runtime);
      throw new ApiError(0, "file_mismatch",
                         "The selected file SHA-256 does not match this upload.",
                         false, null);
    }
    runtime.file = file;
    runtime.paused = false;
    runtime.stop = false;
    runtime.view = publicTask(detail, "uploading");
    this.emit(runtime);
    await this.uploadMissing(runtime);
    return runtime.view;
  }

  requireRuntime(taskId) {
    const runtime = this.runtimes.get(taskId);
    if (!runtime) throw new Error("Upload task is unavailable.");
    return runtime;
  }

  async uploadMissing(runtime) {
    const detail = await getUpload(runtime.view.projectId, runtime.view.taskId);
    const confirmed = new Set(
      detail.confirmed_parts.map((part) => part.part_number)
    );
    runtime.view.confirmedParts = detail.confirmed_parts;
    runtime.view.confirmedBytes = detail.confirmed_parts.reduce(
      (total, part) => total + part.size, 0
    );
    const missing = [];
    for (let partNumber = 0; partNumber < detail.part_count; partNumber += 1) {
      if (!confirmed.has(partNumber)) missing.push(partNumber);
    }
    let cursor = 0;
    let firstError = null;
    const worker = async () => {
      while (!runtime.paused && !runtime.stop && cursor < missing.length) {
        const partNumber = missing[cursor];
        cursor += 1;
        const start = partNumber * detail.chunk_size;
        const blob = runtime.file.slice(start, start + detail.chunk_size);
        try {
          const part = await putPart(runtime.view.projectId, detail.task_id, partNumber,
                                     blob, await hashBlob(blob));
          confirmed.add(part.part_number);
          runtime.view.confirmedParts = runtime.view.confirmedParts.concat(part);
          runtime.view.confirmedBytes += part.size;
          this.emit(runtime);
        } catch (error) {
          firstError = firstError || error;
          runtime.stop = true;
        }
      }
    };
    await Promise.all(Array.from({ length: MAX_PARALLEL_PARTS }, worker));
    if (firstError) {
      runtime.file = null;
      runtime.view.uiState = firstError.retryability ? "interrupted" : "failed";
      runtime.view.failure = firstError.message;
      this.emit(runtime);
      return;
    }
    if (runtime.paused || runtime.stop) return;
    runtime.file = null;
    await this.completeOnce(runtime);
  }

  completeOnce(runtime) {
    if (runtime.completePromise) return runtime.completePromise;
    runtime.view.uiState = "assembling";
    this.emit(runtime);
    runtime.completePromise = completeUpload(runtime.view.projectId,
                                             runtime.view.taskId)
      .then((result) => {
        runtime.view.fileId = result.file_id;
        runtime.view.versionId = result.version_id;
        runtime.view.processingJobId = result.processing_job_id;
        runtime.view.processingPhase = "processing";
        runtime.view.uiState = "completed";
        runtime.file = null;
        this.emit(runtime);
        return result;
      })
      .catch((error) => {
        runtime.completePromise = null;
        runtime.view.uiState = error.retryability ? "interrupted" : "failed";
        runtime.view.failure = error.message;
        this.emit(runtime);
        throw error;
      });
    return runtime.completePromise;
  }
}
