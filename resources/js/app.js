import * as api from "/js/api.js";
import { UploadController } from "/js/uploads.js";

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
  versions: []
};

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
  byId("project-settings").hidden =
    !show || !state.project || state.project.role !== "admin";
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

function canEdit() {
  return state.project && ["admin", "editor"].includes(state.project.role);
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
  state.identity = null;
  state.project = null;
  byId("login-form").hidden = false;
  byId("signed-in").hidden = true;
  byId("password").value = "";
  showProjectSections(false);
  setStatus("global-status", "idle");
}

function renderFiles() {
  const rows = state.files.map((file) => {
    const row = element("tr");
    row.append(element("td", file.name),
               element("td", directoryName(file.directory_id)),
               element("td", file.remote_ai_policy),
               element("td", file.deleted ? "deleted" : "active"));
    const action = element("td");
    const open = element("button", "Open details");
    open.type = "button";
    open.addEventListener("click", () => selectFile(file));
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

async function loadFiles() {
  setStatus("files-status", "loading");
  const result = await api.listFiles(state.project.id, {
    directoryId: byId("directory-filter").value,
    name: byId("name-filter").value.trim(),
    deleted: byId("deleted-filter").checked
  });
  state.files = result.items;
  renderFiles();
}

function renderVersions() {
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
      state.project.id, state.selectedFile.id, version.id
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
  renderRemoteAiApprovals();
}

function renderRemoteAiApprovals() {
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
}

async function selectFile(file) {
  state.selectedFile = file;
  setStatus("detail-status", "loading");
  byId("file-name").value = file.name;
  byId("file-directory").value = file.directory_id;
  byId("file-update-form").hidden = !canEdit() || file.deleted;
  byId("delete-file").hidden = !canEdit() || file.deleted;
  byId("restore-file").hidden = !canEdit() || !file.deleted;
  byId("remote-ai-form").hidden = state.project.role !== "admin" || file.deleted;
  byId("remote-ai-policy").value = file.remote_ai_policy;
  const summary = element("span", `${file.name} · ${file.remote_ai_policy}`);
  const current = element("a", "Download current version");
  current.href = api.currentContentUrl(state.project.id, file.id);
  byId("selected-file-summary").replaceChildren(summary, document.createTextNode(" · "), current);
  try {
    const result = await api.listVersions(state.project.id, file.id);
    state.versions = result.items;
    renderVersions();
    setStatus("detail-status", `${state.versions.length} versions loaded`);
  } catch (error) {
    setStatus("detail-status", errorText(error));
  }
}

function renderMembers(members) {
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
      setStatus("members-status", "loading");
      try {
        await api.setMemberRole(state.project.id, member.user_id, select.value);
        setStatus("members-status", "Member role saved");
      } catch (error) {
        setStatus("members-status", errorText(error));
      }
    });
    card.append(select, save);
    return card;
  });
  byId("member-list").replaceChildren(...cards);
  setStatus("members-status", `${members.length} members loaded`);
}

async function loadProject(project) {
  state.project = project;
  state.selectedFile = null;
  state.versions = [];
  byId("project-role").textContent = project.role;
  showProjectSections(true);
  setStatus("global-status", "loading");
  setStatus("upload-status", "loading");
  try {
    const uploadRefresh = uploads.refresh(project.id);
    const directoryResult = await api.listDirectories(project.id);
    state.directories = directoryResult.items;
    for (const selectId of ["directory-filter", "upload-directory",
                            "file-directory"]) {
      replaceOptions(selectId, state.directories, (directory) => directory.id,
                     (directory) => directory.name,
                     selectId === "directory-filter");
    }
    byId("upload-form").hidden = !canEdit();
    await loadFiles();
    await uploadRefresh;
    if (project.role === "admin") {
      renderMembers((await api.listMembers(project.id)).items);
    } else {
      byId("member-list").replaceChildren();
    }
    setStatus("global-status", "idle");
  } catch (error) {
    setStatus("global-status", errorText(error));
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
  const visible = changed && !changed.taskId
    ? [changed, ...tasks]
    : tasks;
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
    if (task.uiState === "uploading") {
      actions.append(button("Pause", () => uploads.pause(task.taskId), false));
    }
    if (["awaiting reselect", "interrupted", "failed"].includes(task.uiState)) {
      const input = element("input");
      input.type = "file";
      input.setAttribute("aria-label", `Reselect file for upload ${task.taskId}`);
      const resume = button("Reselect and resume", async () => {
        if (!input.files.length) {
          setStatus("upload-status", "Select the original file before resuming.");
          return;
        }
        setStatus("upload-status", "hashing");
        try {
          await uploads.resume(task.taskId, input.files[0]);
        } catch (error) {
          setStatus("upload-status", errorText(error));
        }
      }, false);
      actions.append(input, resume);
    }
    if (!task.taskId || !CANCELLABLE_UPLOAD_STATES.has(task.uiState)) {
      return card;
    }
    actions.append(button("Cancel", async () => {
      try {
        await uploads.cancel(task.taskId);
      } catch (error) {
        setStatus("upload-status", errorText(error));
      }
    }, false));
    card.append(actions);
    return card;
  });
  byId("upload-list").replaceChildren(...cards);
  setStatus("upload-status", visible.length ? "Upload tasks refreshed" : "idle");
}

const uploads = new UploadController(renderUploads);

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
  try {
    await api.logout();
  } finally {
    signedOut();
  }
});

byId("project-select").addEventListener("change", () => {
  const project = state.identity.projects.find(
    (item) => item.id === byId("project-select").value
  );
  if (project) loadProject(project);
});

byId("file-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await loadFiles(); } catch (error) { setStatus("files-status", errorText(error)); }
});
byId("refresh-files").addEventListener("click", async () => {
  try { await loadFiles(); } catch (error) { setStatus("files-status", errorText(error)); }
});

byId("upload-mode").addEventListener("change", () => {
  const createFile = byId("upload-mode").value === "create_file";
  byId("upload-directory").disabled = !createFile;
  byId("upload-file-target").disabled = createFile;
});
byId("upload-mode").dispatchEvent(new Event("change"));

byId("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
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
    byId("upload-file").value = "";
    await loadFiles();
  } catch (error) {
    setStatus("upload-status", errorText(error));
  }
});

byId("file-update-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    state.selectedFile = await api.updateFile(
      state.project.id, state.selectedFile.id, byId("file-name").value,
      byId("file-directory").value
    );
    await loadFiles();
    await selectFile(state.selectedFile);
  } catch (error) {
    setStatus("detail-status", errorText(error));
  }
});

byId("delete-file").addEventListener("click", async () => {
  try {
    await api.softDeleteFile(state.project.id, state.selectedFile.id);
    state.selectedFile.deleted = true;
    await loadFiles();
    await selectFile(state.selectedFile);
  } catch (error) { setStatus("detail-status", errorText(error)); }
});
byId("restore-file").addEventListener("click", async () => {
  try {
    state.selectedFile = await api.restoreFile(state.project.id, state.selectedFile.id);
    await loadFiles();
    await selectFile(state.selectedFile);
  } catch (error) { setStatus("detail-status", errorText(error)); }
});

byId("preview-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const url = api.versionContentUrl(state.project.id, state.selectedFile.id,
                                    byId("preview-version").value);
  const preview = `${url}#page=${Math.max(1, Number(byId("preview-page").value) || 1)}`;
  byId("preview-link").href = preview;
  byId("pdf-preview").src = preview;
});

byId("remote-ai-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const policy = byId("remote-ai-policy").value;
  const approved_version_ids = policy === "remote_ai_allowed"
    ? Array.from(byId("approved-versions").querySelectorAll("input:checked"),
                 (input) => input.value)
    : [];
  try {
    await api.setRemoteAiPolicy(state.project.id, state.selectedFile.id,
                                policy, approved_version_ids);
    state.selectedFile.remote_ai_policy = policy;
    const approved = new Set(approved_version_ids);
    for (const version of state.versions) {
      version.remote_ai_approved = approved.has(version.id);
    }
    renderVersions();
    setStatus("detail-status", "Remote AI policy saved");
  } catch (error) { setStatus("detail-status", errorText(error)); }
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
