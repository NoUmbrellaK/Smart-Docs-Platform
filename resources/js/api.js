const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor(status, code, message, retryability, requestId) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryability = retryability;
    this.requestId = requestId;
  }
}

export async function parseApiResponse(response) {
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_) {
      throw new ApiError(response.status, "invalid_response",
                         "The server returned an invalid response.", false,
                         response.headers.get("X-Request-ID"));
    }
  }
  if (!response.ok) {
    const error = payload && payload.error ? payload.error : {};
    throw new ApiError(response.status, error.code || "request_failed",
                       error.message || response.statusText,
                       Boolean(error.retryable),
                       payload && payload.request_id
                         ? payload.request_id
                         : response.headers.get("X-Request-ID"));
  }
  return payload ? payload.data : null;
}

async function metadata(path, { method = "GET", body, expectedStatus } = {}) {
  const options = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(API_ROOT + path, options);
  const data = await parseApiResponse(response);
  if (expectedStatus !== undefined && response.status !== expectedStatus) {
    throw new ApiError(response.status, "invalid_response",
                       "The server returned an unexpected success response.",
                       false, response.headers.get("X-Request-ID"));
  }
  return data;
}

const id = encodeURIComponent;
const projectPath = (projectId) => `/projects/${id(projectId)}`;

export const login = (username, password) =>
  metadata("/auth/login", { method: "POST", body: { username, password } });
export const logout = () =>
  metadata("/auth/logout", { method: "POST", body: {}, expectedStatus: 204 });
export const me = () => metadata("/me");
export const createProject = (name) =>
  metadata("/projects", { method: "POST", body: { name } });

export const listDirectories = (projectId) =>
  metadata(`${projectPath(projectId)}/directories`);
export const createDirectory = (projectId, parentId, name) =>
  metadata(`${projectPath(projectId)}/directories`, {
    method: "POST", body: { parent_id: parentId, name }
  });
export const renameDirectory = (projectId, directoryId, name) =>
  metadata(`${projectPath(projectId)}/directories/${id(directoryId)}`, {
    method: "PATCH", body: { name }
  });

export function listFiles(projectId, filters = {}) {
  const query = new URLSearchParams();
  if (filters.directoryId) query.set("directory_id", filters.directoryId);
  if (filters.name) query.set("name", filters.name);
  query.set("deleted", String(Boolean(filters.deleted)));
  query.set("page", String(filters.page || 1));
  query.set("page_size", String(filters.pageSize || 100));
  return metadata(`${projectPath(projectId)}/files?${query}`);
}

export const updateFile = (projectId, fileId, name, directoryId) =>
  metadata(`${projectPath(projectId)}/files/${id(fileId)}`, {
    method: "PATCH", body: { name, directory_id: directoryId }
  });
export const softDeleteFile = (projectId, fileId) =>
  metadata(`${projectPath(projectId)}/files/${id(fileId)}`, { method: "DELETE" });
export const restoreFile = (projectId, fileId) =>
  metadata(`${projectPath(projectId)}/files/${id(fileId)}/restore`, {
    method: "POST"
  });
export const listVersions = (projectId, fileId) =>
  metadata(`${projectPath(projectId)}/files/${id(fileId)}/versions`);
export const setRemoteAiPolicy = (projectId, fileId, policy,
                                  approvedVersionIds) =>
  metadata(`${projectPath(projectId)}/files/${id(fileId)}/remote-ai-policy`, {
    method: "PUT",
    body: { policy, approved_version_ids: approvedVersionIds }
  });

export const listMembers = (projectId) =>
  metadata(`${projectPath(projectId)}/members`);
export const setMemberRole = (projectId, userId, role) =>
  metadata(`${projectPath(projectId)}/members/${id(userId)}`, {
    method: "PUT", body: { role }
  });

export const createUpload = (projectId, command) =>
  metadata(`${projectPath(projectId)}/uploads`, {
    method: "POST", body: command
  });
export function listUploads(projectId, filters = {}) {
  const query = new URLSearchParams({
    page: String(filters.page || 1),
    page_size: String(filters.pageSize || 100)
  });
  if (filters.state) query.set("state", filters.state);
  return metadata(`${projectPath(projectId)}/uploads?${query}`);
}
export const getUpload = (projectId, taskId) =>
  metadata(`${projectPath(projectId)}/uploads/${id(taskId)}`);
export const cancelUpload = (projectId, taskId) =>
  metadata(`${projectPath(projectId)}/uploads/${id(taskId)}/cancel`, {
    method: "POST"
  });
export const completeUpload = (projectId, taskId) =>
  metadata(`${projectPath(projectId)}/uploads/${id(taskId)}/complete`, {
    method: "POST"
  });

export const currentContentUrl = (projectId, fileId) =>
  `${API_ROOT}${projectPath(projectId)}/files/${id(fileId)}/content`;
export const versionContentUrl = (projectId, fileId, versionId) =>
  `${API_ROOT}${projectPath(projectId)}/files/${id(fileId)}` +
  `/versions/${id(versionId)}/content`;
