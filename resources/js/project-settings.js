// Local administration only. Approval never starts a model or confirms a move.
export class DirectoryPanel {
  constructor(get, api, errorText, changed, loaded) {
    Object.assign(this, {get, api, errorText, changed, loaded});
    this.sequence = 0; this.writing = false;
    this.control("refresh").addEventListener("click", () => this.load());
    this.control("form").addEventListener("submit", event => {
      event.preventDefault(); return this.write(null, this.control("name"), this.sequence);
    });
    this.setProject(null);
  }
  control(part) { return this.get(`settings-directory-${part}`); }
  node(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  controls() {
    const admin = Boolean(this.project && this.role === "admin"), disabled = !admin || !this.ready || this.writing;
    this.control("form").hidden = !admin;
    this.control("refresh").disabled = !admin || this.writing;
    this.control("editor").disabled = disabled;
    for (const row of this.rows) { row.input.disabled = disabled; row.button.disabled = disabled; }
  }
  clear() {
    this.sequence += 1; this.ready = false; this.directories = []; this.rows = [];
    this.control("rows").replaceChildren(); this.control("parent").replaceChildren();
    this.control("name").value = ""; this.controls();
  }
  setProject(project, role = null) {
    this.project = project; this.role = role; this.clear(); // Only settled writes release the lock.
    this.control("status").textContent = this.writing ? "An earlier directory write is in flight. Wait, then refresh." :
      "Load / refresh directories before editing. Refresh discards unsaved names.";
  }
  async load(message = "") {
    if (!this.project || this.role !== "admin" || this.writing) return;
    this.clear(); const sequence = this.sequence, project = this.project;
    this.changed(project); this.control("status").textContent = `${message} Loading directories…`;
    try {
      const {items} = await this.api.listDirectories(project);
      if (sequence !== this.sequence) return;
      if (!Array.isArray(items) || items.some(d => !d || typeof d.id !== "string" || d.project_id !== project ||
          typeof d.name !== "string" || (d.parent_id !== null && typeof d.parent_id !== "string")) ||
          new Set(items.map(d => d.id)).size !== items.length || items.filter(d => d.parent_id === null).length !== 1 ||
          items.some(d => d.parent_id !== null && !items.some(parent => parent.id === d.parent_id)))
        throw new Error("Directory snapshot is unavailable. Refresh to recheck access.");
      this.directories = items;
      for (const directory of items) {
        const option = this.node("option", `${directory.name} · ${directory.id}`); option.value = directory.id;
        this.control("parent").append(option);
        const card = this.node("div"); card.className = "task-card";
        card.append(this.node("p", `${directory.name} · ${directory.id} · Parent: ${directory.parent_id ?? "none"}`));
        if (directory.parent_id === null) card.append(this.node("p", "Project root cannot be renamed."));
        else {
          const form = this.node("form"), label = this.node("label"), input = this.node("input");
          input.type = "text"; input.value = directory.name; input.required = true; input.maxLength = 255;
          label.append(this.node("span", "New name (up to 255 UTF-8 bytes)"), input);
          const button = this.node("button", "Rename directory"); button.type = "submit";
          form.addEventListener("submit", event => { event.preventDefault(); return this.write(directory, input, sequence); });
          form.append(label, button); card.append(form); this.rows.push({input, button});
        }
        this.control("rows").append(card);
      }
      this.control("parent").value = items.find(d => d.parent_id === null).id;
      this.loaded(project, items); this.ready = true;
      this.control("status").textContent = `${message} ${items.length} directories loaded. Review the current list before any new action. Renaming an approved target revokes its archive approval.`;
    } catch (error) {
      if (sequence === this.sequence) { this.clear(); this.control("status").textContent = `${message} ${this.errorText(error)}`; }
    } finally { this.controls(); }
  }
  async write(directory, input, sequence) {
    if (sequence !== this.sequence || !this.project || this.role !== "admin" || !this.ready || this.writing) return;
    const name = input.value.trim(), parent = this.control("parent").value;
    if (!name || new TextEncoder().encode(name).length > 255 || /[\x00-\x1f\x7f/]/.test(name) ||
        (directory ? directory.parent_id === null || !this.directories.includes(directory) : !this.directories.some(d => d.id === parent))) {
      this.control("status").textContent = "Choose an existing parent and a nonblank name of at most 255 UTF-8 bytes, without slashes or control characters.";
      return;
    }
    const project = this.project;
    this.writing = true; this.controls(); this.changed(project);
    this.control("status").textContent = "Saving directory metadata…";
    let message;
    try {
      if (directory) await this.api.renameDirectory(project, directory.id, name);
      else await this.api.createDirectory(project, parent, name);
      message = directory ? "Directory renamed." : "Directory created.";
    } catch (error) {
      message = `${this.errorText(error)} Outcome may be unknown; no automatic retry. Inspect the refreshed list before a new action.`;
    } finally { this.changed(project); this.writing = false; this.controls(); }
    if (sequence === this.sequence) await this.load(message); // Only read recovery, never replay a write.
  }
}

export class ProjectSettingsPanel {
  constructor(get, api, errorText, changed) {
    Object.assign(this, {get, api, errorText, changed});
    this.states = {checklist: {sequence: 0, writing: false}, archive: {sequence: 0, writing: false}};
    for (const kind of Object.keys(this.states)) {
      this.control(kind, "refresh").addEventListener("click", () => this.load(kind));
      this.control(kind, "form").addEventListener("submit", event => { event.preventDefault(); return this.save(kind); });
    }
    this.control("checklist", "add").addEventListener("click", () => {
      if (!this.editable("checklist")) return;
      if (this.states.checklist.rows.length >= 50) { this.status("checklist", "Use at most 50 checklist items."); return; }
      this.control("checklist", "approval").checked = false;
      this.addItem({key: "", title: "", description: "", required: true});
    });
    this.setProject(null);
  }
  control(kind, part) { return this.get(`settings-${kind}-${part}`); }
  node(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  status(kind, text) { this.control(kind, "status").textContent = text; }
  editable(kind) {
    const state = this.states[kind];
    return this.project && this.role === "admin" && !state.writing && state.revision !== null && state.revision < 4294967295;
  }
  controls(kind) {
    const admin = Boolean(this.project && this.role === "admin");
    this.control(kind, "form").hidden = !admin;
    this.control(kind, "refresh").disabled = !admin || this.states[kind].writing;
    this.control(kind, "editor").disabled = !this.editable(kind);
  }
  clear(kind) {
    const state = this.states[kind];
    state.sequence += 1; state.revision = null; state.rows = [];
    this.control(kind, "rows").replaceChildren();
    this.control(kind, "approval").checked = false;
    this.controls(kind);
  }
  setProject(project, role = null) {
    this.project = project; this.role = role;
    for (const kind of Object.keys(this.states)) {
      this.clear(kind); // In-flight writes retain their lock until the actual request settles.
      this.status(kind, this.states[kind].writing ? "An earlier save is in flight. Wait, then reload the current configuration." :
        "Load / refresh to inspect the current revision. Refresh discards unsaved edits.");
    }
  }
  async load(kind) {
    const state = this.states[kind];
    if (!this.project || this.role !== "admin" || state.writing) return;
    this.clear(kind); const sequence = state.sequence, project = this.project;
    this.status(kind, "Loading configuration…");
    try {
      const snapshot = kind === "checklist" ? await this.api.getHandoverChecklist(project) : await this.api.getArchivePolicy(project);
      if (sequence !== state.sequence) return;
      const items = kind === "checklist" ? snapshot?.items : snapshot?.targets;
      if (!snapshot || !Number.isInteger(snapshot.revision) || snapshot.revision < 0 || snapshot.revision > 4294967295 ||
          typeof snapshot.remote_ai_approved !== "boolean" || !Array.isArray(items) || items.length > (kind === "checklist" ? 50 : 100))
        throw new Error("Configuration is unavailable.");
      let directories;
      if (kind === "archive") {
        directories = (await this.api.listDirectories(project)).items;
        if (sequence !== state.sequence) return;
        if (!Array.isArray(directories) || items.some(item => !directories.some(d => d.id === item.directory_id)))
          throw new Error("A configured directory is unavailable. Reload to recheck access.");
      }
      state.revision = snapshot.revision;
      if (kind === "checklist") for (const item of items) this.addItem(item);
      else for (const directory of directories) {
        const label = this.node("label"); label.className = "checkbox-label";
        const checkbox = this.node("input"); checkbox.type = "checkbox";
        checkbox.checked = items.some(item => item.directory_id === directory.id);
        checkbox.addEventListener("change", () => {
          if (sequence === state.sequence) this.control(kind, "approval").checked = false;
        });
        label.append(checkbox, this.node("span", `${directory.name} · ${directory.id}`));
        this.control(kind, "rows").append(label);
        state.rows.push({checkbox, directory_id: directory.id, name: directory.name});
      }
      this.control(kind, "approval").checked = false;
      this.status(kind, `Revision ${snapshot.revision} · Currently ${snapshot.remote_ai_approved ? "approved" : "not approved"} for remote AI. ` +
        "Saving replaces the complete set and requires fresh approval; unchecked revokes approval. Current directory names are shown. Existing bound tasks may become unavailable.");
    } catch (error) {
      if (sequence === state.sequence) { this.clear(kind); this.status(kind, this.errorText(error)); }
    } finally { this.controls(kind); }
  }
  addItem(item) {
    const state = this.states.checklist, sequence = state.sequence;
    const row = {card: this.node("div"), fields: {}}; row.card.className = "task-card checklist-item";
    for (const [name, title, limit] of [["key", "Stable key (lowercase letters, digits, underscores)", 64],
      ["title", "Title (up to 200 UTF-8 bytes)", 200], ["description", "Description (one line, up to 1000 UTF-8 bytes)", 1000], ["required", "Required", null]]) {
      const label = this.node("label"), input = this.node("input");
      input.name = name; input.type = name === "required" ? "checkbox" : "text";
      if (name === "required") { input.checked = item.required; label.className = "checkbox-label"; }
      else { input.value = item[name]; input.maxLength = limit; input.required = name !== "description"; }
      input.addEventListener(name === "required" ? "change" : "input", () => {
        if (sequence === state.sequence) this.control("checklist", "approval").checked = false;
      });
      label.append(this.node("span", title), input); row.card.append(label); row.fields[name] = input;
    }
    const remove = this.node("button", "Remove checklist item"); remove.type = "button";
    remove.addEventListener("click", () => {
      if (sequence !== state.sequence || !this.editable("checklist")) return;
      state.rows = state.rows.filter(item => item !== row);
      this.control("checklist", "approval").checked = false;
      this.control("checklist", "rows").replaceChildren(...state.rows.map(item => item.card));
    });
    row.card.append(remove); state.rows.push(row); this.control("checklist", "rows").append(row.card);
  }
  command(kind) {
    const state = this.states[kind];
    const command = {expected_revision: state.revision, confirm_remote_ai: this.control(kind, "approval").checked};
    if (kind === "checklist") {
      command.items = state.rows.map(({fields: f}) => ({key: f.key.value, title: f.title.value,
        description: f.description.value, required: f.required.checked}));
      const keys = new Set();
      const validText = (s, max) => new TextEncoder().encode(s).length <= max && !/[\x00-\x1f\x7f]/.test(s);
      for (const item of command.items) {
        if (!/^[a-z][a-z0-9_]{0,63}$/.test(item.key) || keys.has(item.key) || !item.title.trim() ||
            !validText(item.title, 200) || !validText(item.description, 1000))
          throw new Error("Use unique lowercase keys, nonblank titles and the UTF-8 limits. Control characters are not allowed.");
        keys.add(item.key);
      }
    } else {
      command.targets = state.rows.filter(row => row.checkbox.checked).map(({directory_id, name}) => ({directory_id, name}));
      if (command.targets.length > 100) throw new Error("Select at most 100 archive directories.");
    }
    return command;
  }
  async save(kind) {
    if (!this.editable(kind)) return;
    let command;
    try { command = this.command(kind); }
    catch (error) { this.status(kind, this.errorText(error)); return; }
    const state = this.states[kind], sequence = state.sequence, project = this.project;
    state.writing = true; this.controls(kind); this.status(kind, "Saving this exact revision…"); this.changed(project);
    try {
      const result = kind === "checklist" ? await this.api.setHandoverChecklist(project, command) : await this.api.setArchivePolicy(project, command);
      if (sequence !== state.sequence) return;
      this.clear(kind);
      this.status(kind, `Saved revision ${result.revision}. Reload before editing again. No model request or file move was started.`);
    } catch (error) {
      if (sequence === state.sequence) {
        this.clear(kind);
        this.status(kind, `${this.errorText(error)} Save outcome may be unknown. Reload and inspect the actual revision before editing; no automatic retry.`);
      }
    } finally { this.changed(project); state.writing = false; this.controls(kind); }
  }
}
