import * as api from "./api.js";
import { UploadController } from "./uploads.js";

export class ProjectGeneration {
  constructor() {
    this.generation = 0;
    this.projectId = null;
  }

  begin(projectId) {
    this.generation += 1;
    this.projectId = projectId;
    return Object.freeze({ generation: this.generation, projectId });
  }

  current(token) {
    return token && token.generation === this.generation &&
      token.projectId === this.projectId;
  }

  commit(token, effect) {
    if (!this.current(token)) return false;
    effect();
    return true;
  }

  action(token, operation) {
    return async (...args) => {
      if (!this.current(token)) return false;
      await operation(token.projectId, ...args);
      return this.current(token);
    };
  }
}

export function writeVisibility(role, ready, selectedFile) {
  const editable = ready && (role === "admin" || role === "editor");
  const active = editable && selectedFile && !selectedFile.deleted;
  const deleted = editable && selectedFile && selectedFile.deleted;
  return {
    upload: editable,
    update: Boolean(active),
    delete: Boolean(active),
    restore: Boolean(deleted),
    remoteAi: Boolean(ready && role === "admin" && selectedFile &&
      !selectedFile.deleted),
    members: Boolean(ready && role === "admin")
  };
}

export function synchronizeSelfRole(identity, project, memberUserId,
                                    response, ready, selectedFile) {
  const confirmedRole = response && response.role;
  const isSelf = identity && identity.user &&
    memberUserId === identity.user.id;
  if (!isSelf || !confirmedRole) {
    return {
      changed: false,
      identity,
      project,
      visibility: writeVisibility(project && project.role, ready, selectedFile)
    };
  }
  const synchronizedProject = { ...project, role: confirmedRole };
  const synchronizedIdentity = {
    ...identity,
    projects: identity.projects.map((membership) =>
      membership.id === project.id
        ? { ...membership, role: confirmedRole }
        : membership)
  };
  return {
    changed: true,
    identity: synchronizedIdentity,
    project: synchronizedProject,
    visibility: writeVisibility(confirmedRole, ready, selectedFile)
  };
}

export async function performLogout(logout, onSignedOut, onError) {
  try {
    await logout();
    onSignedOut();
    return true;
  } catch (error) {
    if (error instanceof api.ApiError && error.status === 401) {
      onSignedOut();
      return true;
    }
    onError(errorText(error));
    return false;
  }
}

const byId = (id) => document.getElementById(id);
const CANCELLABLE_UPLOAD_STATES = new Set([
  "awaiting reselect", "uploading", "interrupted", "failed"
]);
const state = {
  identity: null,
  project: null,
  directories: [],
  files: [],
  selectedFile: null,
  versions: [],
  projectReady: false,
  projectToken: null,
  selectedFileReady: false,
  fileToken: null
};
const projectLoads = new ProjectGeneration();
const fileLoads = new ProjectGeneration();
const fileListLoads = new ProjectGeneration();
let uploads = null;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function setStatus(target, value) {
  byId(target).textContent = value;
}

function errorText(error) {
  if (error instanceof api.ApiError) {
    const request = error.requestId ? ` Request ID: ${error.requestId}.` : "";
    return `${error.code}: ${error.message}${request}`;
  }
  return error && error.message ? error.message : "Unexpected browser error.";
}

function showProjectSections(show) {
  for (const id of ["workbench", "upload-tasks", "file-detail"]) {
    byId(id).hidden = !show;
  }
  byId("project-settings").hidden = true;
}

function hideAndDisable(id, hidden) {
  const node = byId(id);
  node.hidden = hidden;
  for (const control of node.matches("button, input, select, textarea")
    ? [node]
    : node.querySelectorAll("button, input, select, textarea")) {
    control.disabled = hidden;
  }
}

function applyWriteVisibility() {
  const visibility = writeVisibility(
    state.project ? state.project.role : null,
    state.projectReady,
    state.selectedFileReady ? state.selectedFile : null
  );
  hideAndDisable("upload-form", !visibility.upload);
  hideAndDisable("file-update-form", !visibility.update);
  hideAndDisable("delete-file", !visibility.delete);
  hideAndDisable("restore-file", !visibility.restore);
  hideAndDisable("remote-ai-form", !visibility.remoteAi);
  hideAndDisable("member-list", !visibility.members);
  byId("project-settings").hidden = !visibility.members;
}

function resetProjectView(project) {
  state.project = project;
  state.projectReady = false;
  state.directories = [];
  state.files = [];
  state.selectedFile = null;
  state.selectedFileReady = false;
  state.fileToken = fileLoads.begin(null);
  fileListLoads.begin(null);
  state.versions = [];
  byId("project-role").textContent = project.role;
  byId("file-rows").replaceChildren();
  byId("version-list").replaceChildren();
  byId("approved-versions").replaceChildren();
  byId("member-list").replaceChildren();
  byId("upload-list").replaceChildren();
  byId("selected-file-summary").textContent =
    "Choose a file from the workbench.";
  for (const selectId of ["directory-filter", "upload-directory",
                          "upload-file-target", "file-directory",
                          "preview-version"]) {
    byId(selectId).replaceChildren();
  }
  byId("preview-form").hidden = true;
  byId("preview-link").hidden = true;
  byId("pdf-preview").removeAttribute("src");
  applyWriteVisibility();
}

function replaceOptions(selectId, items, value, label, includeAll) {
  const select = byId(selectId);
  const options = [];
  if (includeAll) {
    const all = element("option", "All directories");
    all.value = "";
    options.push(all);
  }
  for (const item of items) {
    const option = element("option", label(item));
    option.value = value(item);
    options.push(option);
  }
  select.replaceChildren(...options);
}

function directoryName(directoryId) {
  const directory = state.directories.find((item) => item.id === directoryId);
  return directory ? directory.name : directoryId;
}

function renderIdentity(identity) {
  state.identity = identity;
  byId("login-form").hidden = true;
  byId("signed-in").hidden = false;
  byId("current-user").textContent = identity.user.username;
  replaceOptions("project-select", identity.projects,
                 (project) => project.id,
                 (project) => project.name, false);
  if (identity.projects.length === 0) {
    state.project = null;
    showProjectSections(false);
    setStatus("global-status", "No accessible projects");
    return;
  }
  const current = identity.projects.find(
    (project) => state.project && project.id === state.project.id
  ) || identity.projects[0];
  byId("project-select").value = current.id;
  loadProject(current);
}

function signedOut() {
  projectLoads.begin(null);
  state.identity = null;
  state.project = null;
  state.projectToken = null;
  state.projectReady = false;
  state.selectedFile = null;
  state.selectedFileReady = false;
  state.fileToken = fileLoads.begin(null);
  fileListLoads.begin(null);
  byId("login-form").hidden = false;
  byId("signed-in").hidden = true;
  byId("password").value = "";
  showProjectSections(false);
  applyWriteVisibility();
  setStatus("global-status", "idle");
}

function renderFiles(token) {
  if (!projectLoads.current(token)) return;
  const rows = state.files.map((file) => {
    const row = element("tr");
    row.append(element("td", file.name),
               element("td", directoryName(file.directory_id)),
               element("td", file.remote_ai_policy),
               element("td", file.deleted ? "deleted" : "active"));
    const action = element("td");
    const open = element("button", "Open details");
    open.type = "button";
    open.addEventListener("click", () => selectFile(file, token));
    action.append(open);
    row.append(action);
    return row;
  });
  byId("file-rows").replaceChildren(...rows);
  setStatus("files-status", `${state.files.length} files loaded`);
  replaceOptions("upload-file-target",
                 state.files.filter((file) => !file.deleted),
                 (file) => file.id, (file) => file.name, false);
}

async function loadFiles(token = state.projectToken) {
  if (!projectLoads.current(token)) return false;
  const listToken = fileListLoads.begin(token.projectId);
  const filters = {
    directoryId: byId("directory-filter").value,
    name: byId("name-filter").value.trim(),
    deleted: byId("deleted-filter").checked
  };
  setStatus("files-status", "loading");
  const result = await api.listFiles(token.projectId, filters);
  if (!projectLoads.current(token) || !fileListLoads.current(listToken)) {
    return false;
  }
  state.files = result.items;
  renderFiles(token);
  return true;
}

function currentFile(projectToken, fileToken) {
  return projectLoads.current(projectToken) && fileLoads.current(fileToken) &&
    state.selectedFile && state.selectedFile.id === fileToken.projectId;
}

function renderVersions(projectToken, fileToken) {
  const fileId = fileToken.projectId;
  if (!currentFile(projectToken, fileToken)) return;
  const cards = state.versions.map((version) => {
    const card = element("article", undefined, "version-card");
    card.append(element("h4", `Version ${version.version_number}`));
    card.append(element("p", `${version.size} bytes · ${version.media_type}`));
    card.append(element("p", `Processing: ${version.processing_state}`));
    card.append(element("p", `SHA-256: ${version.sha256}`));
    card.append(element("p", version.remote_ai_approved
      ? "Remote AI approved" : "Remote AI not approved"));
    const download = element("a", "Download this exact version");
    download.href = api.versionContentUrl(
      projectToken.projectId, fileId, version.id
    );
    card.append(download);
    return card;
  });
  byId("version-list").replaceChildren(...cards);

  const pdfVersions = state.versions.filter(
    (version) => version.media_type === "application/pdf"
  );
  replaceOptions("preview-version", pdfVersions, (version) => version.id,
                 (version) => `Version ${version.version_number}`, false);
  byId("preview-form").hidden = pdfVersions.length === 0;
  byId("preview-link").hidden = pdfVersions.length === 0;
  renderRemoteAiApprovals(projectToken, fileToken);
}

function renderRemoteAiApprovals(projectToken, fileToken) {
  if (!currentFile(projectToken, fileToken)) return;
  const container = byId("approved-versions");
  const controls = state.versions.map((version) => {
    const label = element("label", undefined, "checkbox-label");
    label.htmlFor = `approved-${version.id}`;
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.id = `approved-${version.id}`;
    checkbox.value = version.id;
    checkbox.checked = version.remote_ai_approved;
    label.append(checkbox, document.createTextNode(
      `Approve version ${version.version_number}`
    ));
    return label;
  });
  container.replaceChildren(...controls);
  applyWriteVisibility();
}

async function selectFile(file, token = state.projectToken) {
  if (!projectLoads.current(token)) return false;
  const fileToken = fileLoads.begin(file.id);
  state.fileToken = fileToken;
  state.selectedFile = file;
  state.selectedFileReady = false;
  state.versions = [];
  byId("version-list").replaceChildren();
  byId("approved-versions").replaceChildren();
  byId("preview-form").hidden = true;
  byId("preview-link").hidden = true;
  byId("pdf-preview").removeAttribute("src");
  setStatus("detail-status", "loading");
  byId("file-name").value = file.name;
  byId("file-directory").value = file.directory_id;
  applyWriteVisibility();
  byId("remote-ai-policy").value = file.remote_ai_policy;
  const summary = element("span", `${file.name} · ${file.remote_ai_policy}`);
  const current = element("a", "Download current version");
  current.href = api.currentContentUrl(token.projectId, file.id);
  byId("selected-file-summary").replaceChildren(summary, document.createTextNode(" · "), current);
  try {
    const result = await api.listVersions(token.projectId, file.id);
    if (!currentFile(token, fileToken)) return false;
    state.versions = result.items;
    state.selectedFileReady = true;
    applyWriteVisibility();
    renderVersions(token, fileToken);
    setStatus("detail-status", `${state.versions.length} versions loaded`);
    return true;
  } catch (error) {
    if (currentFile(token, fileToken)) {
      setStatus("detail-status", errorText(error));
    }
    return false;
  }
}

function renderMembers(members, token) {
  if (!projectLoads.current(token)) return;
  const cards = members.map((member) => {
    const card = element("article", undefined, "member-card");
    card.append(element("p", `${member.username} · ${member.user_id}`));
    const select = element("select");
    select.setAttribute("aria-label", `Role for ${member.username}`);
    for (const role of ["reader", "editor", "admin"]) {
      const option = element("option", role);
      option.value = role;
      select.append(option);
    }
    select.value = member.role;
    const save = element("button", "Save role");
    save.type = "button";
    save.addEventListener("click", async () => {
      if (!projectLoads.current(token)) return;
      save.disabled = true;
      setStatus("members-status", "loading");
      try {
        const response = await api.setMemberRole(
          token.projectId, member.user_id, select.value
        );
        if (!projectLoads.current(token)) return;
        const synchronized = synchronizeSelfRole(
          state.identity, state.project, member.user_id, response,
          state.projectReady,
          state.selectedFileReady ? state.selectedFile : null
        );
        if (synchronized.changed) {
          state.identity = synchronized.identity;
          state.project = synchronized.project;
          byId("project-role").textContent = synchronized.project.role;
          applyWriteVisibility();
          renderUploads(uploads.snapshot());
        }
        setStatus("members-status", "Member role saved");
      } catch (error) {
        if (projectLoads.current(token)) {
          setStatus("members-status", errorText(error));
        }
      } finally {
        if (projectLoads.current(token) &&
            writeVisibility(state.project.role, state.projectReady, null).members) {
          save.disabled = false;
        }
      }
    });
    card.append(select, save);
    return card;
  });
  byId("member-list").replaceChildren(...cards);
  setStatus("members-status", `${members.length} members loaded`);
}

async function loadProject(project) {
  const token = projectLoads.begin(project.id);
  state.projectToken = token;
  resetProjectView(project);
  showProjectSections(true);
  setStatus("global-status", "loading");
  setStatus("upload-status", "loading");
  try {
    const uploadRefresh = uploads.refresh(project.id).then(
      () => null, (error) => error
    );
    const directoryResult = await api.listDirectories(token.projectId);
    if (!projectLoads.current(token)) return;
    state.directories = directoryResult.items;
    for (const selectId of ["directory-filter", "upload-directory",
                            "file-directory"]) {
      replaceOptions(selectId, state.directories, (directory) => directory.id,
                     (directory) => directory.name,
                     selectId === "directory-filter");
    }
    if (!await loadFiles(token)) return;
    if (!projectLoads.current(token)) return;
    const uploadError = await uploadRefresh;
    if (!projectLoads.current(token)) return;
    if (uploadError) throw uploadError;
    if (project.role === "admin") {
      const members = await api.listMembers(token.projectId);
      if (!projectLoads.current(token)) return;
      renderMembers(members.items, token);
    } else {
      byId("member-list").replaceChildren();
    }
    state.projectReady = true;
    applyWriteVisibility();
    renderUploads(uploads.snapshot());
    setStatus("global-status", "idle");
  } catch (error) {
    if (projectLoads.current(token)) {
      setStatus("global-status", errorText(error));
      applyWriteVisibility();
    }
  }
}

function button(text, action, disabled) {
  const node = element("button", text);
  node.type = "button";
  node.disabled = disabled;
  node.addEventListener("click", action);
  return node;
}

function renderUploads(tasks, changed) {
  const projectId = state.project && state.project.id;
  const token = state.projectToken;
  const projectTasks = tasks.filter((task) => task.projectId === projectId);
  const visible = changed && changed.projectId === projectId && !changed.taskId
    ? [changed, ...projectTasks]
    : projectTasks;
  const mayWrite = writeVisibility(
    state.project ? state.project.role : null, state.projectReady, null
  ).upload;
  const cards = visible.map((task) => {
    const card = element("article", undefined, "task-card");
    card.append(element("h3", task.name || task.taskId || "Preparing upload"));
    card.append(element("p", `State: ${task.uiState}`));
    card.append(element("p", `Confirmed: ${task.confirmedBytes || 0} of ${task.size} bytes`));
    if (task.failure) card.append(element("p", `Failure: ${task.failure}`));
    if (task.uiState === "completed") {
      card.append(element("p", `File ID: ${task.fileId}`),
                  element("p", `Version ID: ${task.versionId}`),
                  element("p", `Processing job ID: ${task.processingJobId}`),
                  element("p", `Phase: ${task.processingPhase}`));
      return card;
    }
    const actions = element("div", undefined, "task-actions");
    if (mayWrite && task.uiState === "uploading") {
      actions.append(button("Pause", () => uploads.pause(task.taskId), false));
    }
    if (mayWrite && ["awaiting reselect", "interrupted", "failed"].includes(task.uiState)) {
      const input = element("input");
      input.type = "file";
      input.setAttribute("aria-label", `Reselect file for upload ${task.taskId}`);
      const resume = button("Reselect and resume", async () => {
        if (!projectLoads.current(token)) return;
        if (!input.files.length) {
          setStatus("upload-status", "Select the original file before resuming.");
          return;
        }
        setStatus("upload-status", "hashing");
        try {
          await uploads.resume(task.taskId, input.files[0]);
        } catch (error) {
          if (projectLoads.current(token)) {
            setStatus("upload-status", errorText(error));
          }
        }
      }, false);
      actions.append(input, resume);
    }
    if (!mayWrite || !task.taskId ||
        !CANCELLABLE_UPLOAD_STATES.has(task.uiState)) {
      return card;
    }
    actions.append(button("Cancel", async () => {
      if (!projectLoads.current(token)) return;
      try {
        await uploads.cancel(task.taskId);
      } catch (error) {
        if (projectLoads.current(token)) {
          setStatus("upload-status", errorText(error));
        }
      }
    }, false));
    card.append(actions);
    return card;
  });
  byId("upload-list").replaceChildren(...cards);
  setStatus("upload-status", visible.length ? "Upload tasks refreshed" : "idle");
}

function bootstrap() {
uploads = new UploadController(renderUploads);

byId("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("session-error", "");
  setStatus("global-status", "loading");
  try {
    renderIdentity(await api.login(byId("username").value,
                                   byId("password").value));
  } catch (error) {
    setStatus("session-error", errorText(error));
    setStatus("global-status", "idle");
  }
});

byId("logout-button").addEventListener("click", async () => {
  setStatus("session-error", "");
  setStatus("global-status", "loading");
  await performLogout(api.logout, signedOut, (message) => {
    setStatus("session-error", message);
    setStatus("global-status", "idle");
  });
});

byId("project-select").addEventListener("change", () => {
  const project = state.identity.projects.find(
    (item) => item.id === byId("project-select").value
  );
  if (project) loadProject(project);
});

byId("file-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.projectReady) return;
  const token = state.projectToken;
  try {
    await loadFiles(token);
  } catch (error) {
    if (projectLoads.current(token)) setStatus("files-status", errorText(error));
  }
});
byId("refresh-files").addEventListener("click", async () => {
  if (!state.projectReady) return;
  const token = state.projectToken;
  try {
    await loadFiles(token);
  } catch (error) {
    if (projectLoads.current(token)) setStatus("files-status", errorText(error));
  }
});

byId("upload-mode").addEventListener("change", () => {
  const createFile = byId("upload-mode").value === "create_file";
  byId("upload-directory").disabled = !createFile;
  byId("upload-file-target").disabled = createFile;
});
byId("upload-mode").dispatchEvent(new Event("change"));

byId("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = state.projectToken;
  if (!projectLoads.current(token)) return;
  const file = byId("upload-file").files[0];
  if (!file) return;
  const createFile = byId("upload-mode").value === "create_file";
  const target = state.files.find(
    (item) => item.id === byId("upload-file-target").value
  );
  if (!createFile && !target) {
    setStatus("upload-status", "Choose an existing file for the new version.");
    return;
  }
  const command = createFile
    ? { mode: "create_file", directory_id: byId("upload-directory").value,
        name: file.name }
    : { mode: "create_version", file_id: target.id,
        observed_current_version_id: target.current_version_id };
  setStatus("upload-status", "hashing");
  try {
    await uploads.start(file, command);
    if (!projectLoads.current(token)) return;
    byId("upload-file").value = "";
    await loadFiles(token);
  } catch (error) {
    if (projectLoads.current(token)) {
      setStatus("upload-status", errorText(error));
    }
  }
});

byId("file-update-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = state.projectToken;
  const fileToken = state.fileToken;
  const fileId = state.selectedFile && state.selectedFile.id;
  if (!currentFile(token, fileToken) || !state.selectedFileReady) return;
  try {
    const updated = await api.updateFile(
      token.projectId, fileId, byId("file-name").value,
      byId("file-directory").value
    );
    if (!currentFile(token, fileToken)) return;
    state.selectedFile = updated;
    state.selectedFileReady = false;
    applyWriteVisibility();
    if (!await loadFiles(token)) return;
    if (!currentFile(token, fileToken)) return;
    await selectFile(updated, token);
  } catch (error) {
    if (currentFile(token, fileToken)) {
      setStatus("detail-status", errorText(error));
    }
  }
});

byId("delete-file").addEventListener("click", async () => {
  const token = state.projectToken;
  const fileToken = state.fileToken;
  const selected = state.selectedFile;
  if (!currentFile(token, fileToken) || !state.selectedFileReady) return;
  try {
    await api.softDeleteFile(token.projectId, selected.id);
    if (!currentFile(token, fileToken)) return;
    const deleted = { ...selected, deleted: true };
    state.selectedFile = deleted;
    state.selectedFileReady = false;
    applyWriteVisibility();
    if (!await loadFiles(token)) return;
    if (!currentFile(token, fileToken)) return;
    await selectFile(deleted, token);
  } catch (error) {
    if (currentFile(token, fileToken)) setStatus("detail-status", errorText(error));
  }
});
byId("restore-file").addEventListener("click", async () => {
  const token = state.projectToken;
  const fileToken = state.fileToken;
  const fileId = state.selectedFile && state.selectedFile.id;
  if (!currentFile(token, fileToken) || !state.selectedFileReady) return;
  try {
    const restored = await api.restoreFile(token.projectId, fileId);
    if (!currentFile(token, fileToken)) return;
    state.selectedFile = restored;
    state.selectedFileReady = false;
    applyWriteVisibility();
    if (!await loadFiles(token)) return;
    if (!currentFile(token, fileToken)) return;
    await selectFile(restored, token);
  } catch (error) {
    if (currentFile(token, fileToken)) setStatus("detail-status", errorText(error));
  }
});

byId("preview-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const token = state.projectToken;
  const fileToken = state.fileToken;
  if (!currentFile(token, fileToken) || !state.selectedFileReady) return;
  const url = api.versionContentUrl(token.projectId, state.selectedFile.id,
                                    byId("preview-version").value);
  const preview = `${url}#page=${Math.max(1, Number(byId("preview-page").value) || 1)}`;
  byId("preview-link").href = preview;
  byId("pdf-preview").src = preview;
});

byId("remote-ai-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = state.projectToken;
  const fileToken = state.fileToken;
  const fileId = state.selectedFile && state.selectedFile.id;
  if (!currentFile(token, fileToken) || !state.selectedFileReady) return;
  const policy = byId("remote-ai-policy").value;
  const approved_version_ids = policy === "remote_ai_allowed"
    ? Array.from(byId("approved-versions").querySelectorAll("input:checked"),
                 (input) => input.value)
    : [];
  try {
    await api.setRemoteAiPolicy(token.projectId, fileId,
                                policy, approved_version_ids);
    if (!currentFile(token, fileToken)) return;
    state.selectedFile.remote_ai_policy = policy;
    const approved = new Set(approved_version_ids);
    for (const version of state.versions) {
      version.remote_ai_approved = approved.has(version.id);
    }
    renderVersions(token, fileToken);
    setStatus("detail-status", "Remote AI policy saved");
  } catch (error) {
    if (currentFile(token, fileToken)) setStatus("detail-status", errorText(error));
  }
});

async function initialize() {
  setStatus("global-status", "loading");
  try {
    renderIdentity(await api.me());
  } catch (error) {
    if (error instanceof api.ApiError && error.status === 401) {
      signedOut();
    } else {
      signedOut();
      setStatus("session-error", errorText(error));
    }
  }
}

initialize();
}

if (typeof document !== "undefined") bootstrap();
