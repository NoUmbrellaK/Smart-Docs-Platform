// User-driven snapshots. Creating a task is not starting a model request.
export class AgentPanel {
  constructor(get, api, errorText) {
    Object.assign(this, {get, api, errorText});
    this.generation = 0;
    this.listGeneration = 0;
    this.detailGeneration = 0;
    this.busy = false; // Cancellation lock: only the settled request may release it.
    this.creating = false;
    this.archiveWriting = false;
    this.archiveGeneration = 0;
    this.uncertain = false;
    get("agent-form").addEventListener("submit", event => { event.preventDefault(); return this.create(); });
    get("agent-question").addEventListener("input", () => { get("agent-consent").checked = false; });
    get("agent-new-draft").addEventListener("click", () => { if (!this.busy) this.newDraft(); });
    get("agent-refresh").addEventListener("click", () => this.refresh());
    get("agent-next").addEventListener("click", () => { if (this.next) return this.refresh(this.next); });
    this.setProject(null);
  }

  node(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  button(text, action) {
    const button = this.node("button", text);
    button.type = "button";
    button.addEventListener("click", action);
    return button;
  }
  status(part, text) { this.get(`agent-${part}-status`).textContent = text; }
  controls() {
    const blocked = !this.project || this.busy || this.creating || this.archiveWriting;
    for (const id of ["question", "consent", "new-draft"]) this.get(`agent-${id}`).disabled = blocked;
    this.get("agent-create").disabled = blocked || this.uncertain;
    this.get("agent-refresh").disabled = blocked;
    this.get("agent-next").disabled = blocked || !this.next;
  }
  clearDraft() {
    this.scope = new Map();
    this.get("agent-question").value = "";
    this.get("agent-consent").checked = false;
    this.get("agent-scope").replaceChildren();
  }
  newDraft() {
    if (this.creating || this.archiveWriting) return;
    this.uncertain = false;
    this.clearDraft();
    this.status("create", "Select approved current versions in File detail. A new draft does not retry an earlier task.");
    this.controls();
  }
  setProject(project, role = "reader") {
    this.project = project;
    this.role = role;
    this.invalidate();
  }
  invalidate() {
    this.generation += 1;
    this.listGeneration += 1;
    this.detailGeneration += 1;
    this.clearArchive();
    this.next = null;
    this.clearDraft();
    this.status("create", this.creating ? "A create request is still in flight; wait and check your tasks." :
      this.uncertain ? "An earlier creation outcome may be unknown. Check your tasks before explicitly starting a new draft." :
      "Select approved current versions in File detail.");
    this.controls();
    for (const id of ["list", "detail", "source", "audit"]) this.get(`agent-${id}`).replaceChildren();
    this.status("audit", "Select a task to load its tool calls.");
    this.status("list", "Refresh to load your tasks.");
    this.status("detail", "Select a task to check its current state.");
  }
  addVersion(project, file, version) {
    if (!this.project || project !== this.project || this.busy || this.creating || this.archiveWriting) return;
    if (file.deleted || file.remote_ai_policy !== "remote_ai_allowed" ||
        file.current_version_id !== version.id || !version.remote_ai_approved) {
      this.status("create", "Only an explicitly approved current version can be selected."); return;
    }
    if (!this.scope.has(file.id) && this.scope.size >= 100) {
      this.status("create", "Select at most 100 files."); return;
    }
    this.scope.set(file.id, {file_id: file.id, version_id: version.id, name: file.name});
    this.get("agent-consent").checked = false;
    this.renderScope();
  }
  renderScope() {
    const generation = this.generation;
    this.get("agent-scope").replaceChildren(...Array.from(this.scope.values(), item => {
      const card = this.node("article"); card.className = "task-card";
      card.append(this.node("p", `${item.name} · File ${item.file_id} · Version ${item.version_id}`),
        this.button("Remove from task", () => {
          if (generation !== this.generation || this.busy || this.creating || this.archiveWriting) return;
          this.scope.delete(item.file_id); this.get("agent-consent").checked = false; this.renderScope();
        }));
      return card;
    }));
  }
  async create() {
    if (!this.project || this.busy || this.creating || this.archiveWriting || this.uncertain) return;
    const question = this.get("agent-question").value;
    if (!question.trim() || new TextEncoder().encode(question).length > 4000 ||
        /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(question) ||
        !this.scope.size || !this.get("agent-consent").checked) {
      this.status("create", "Enter 1–4000 UTF-8 bytes, select versions and explicitly confirm remote AI use."); return;
    }
    const generation = this.generation, project = this.project;
    const versions = Array.from(this.scope.values(), ({file_id, version_id}) => ({file_id, version_id}));
    this.creating = true; this.controls(); this.status("create", "Creating pending task…");
    try {
      const task = await this.api.createAgentTask(project, {question, versions, confirm_remote_ai: true});
      if (generation !== this.generation) {
        this.uncertain = true;
        this.status("create", "An earlier creation outcome may be unknown in this view. Check your tasks before explicitly starting a new draft.");
        return;
      }
      this.clearDraft();
      this.status("create", `Created task ${task.id} · pending, waiting for an executor. This page does not start model execution.`);
    } catch (error) {
      this.uncertain = true;
      this.get("agent-consent").checked = false;
      this.status("create", `${generation === this.generation ? this.errorText(error) : ""} Creation outcome may be unknown. Refresh your tasks and check before explicitly starting a new draft; no automatic retry.`);
    } finally {
      this.creating = false; this.controls();
    }
    if (generation === this.generation) await this.refresh();
  }
  clearDetail() {
    this.detailGeneration += 1;
    this.clearArchive();
    this.get("agent-detail").replaceChildren(); this.get("agent-source").replaceChildren();
    this.get("agent-audit").replaceChildren();
    this.status("audit", "Select a task and load its tool calls; no automatic polling.");
    this.status("detail", "Select a task to check its current state.");
  }
  async refresh(before = "") {
    if (!this.project || this.busy || this.creating || this.archiveWriting) return;
    const generation = this.generation, list = ++this.listGeneration, project = this.project;
    this.clearDetail(); this.next = null; this.controls();
    this.get("agent-list").replaceChildren(); this.status("list", "Loading your tasks…");
    try {
      const page = await this.api.listAgentTasks(project, before);
      if (generation !== this.generation || list !== this.listGeneration) return;
      this.next = page.next_before;
      this.get("agent-list").replaceChildren(...page.items.map(task => {
        const card = this.node("article"); card.className = "task-card";
        card.append(this.node("p", `Task ${task.id} · ${task.state} · Created ${task.created_at} UTC`),
          this.button("View task", () => {
            if (generation === this.generation && list === this.listGeneration) return this.view(task.id);
          }));
        return card;
      }));
      this.status("list", `${page.items.length} tasks on this page. Snapshot only; Refresh starts from the newest page.`);
    } catch (error) {
      if (generation === this.generation && list === this.listGeneration) this.status("list", this.errorText(error));
    } finally {
      if (generation === this.generation && list === this.listGeneration) this.controls();
    }
  }
  async view(id) {
    if (!this.project || this.busy || this.creating || this.archiveWriting) return;
    this.clearDetail(); const token = this.detailGeneration, project = this.project;
    this.status("detail", "Checking task access…");
    try {
      const task = await this.api.getAgentTask(project, id);
      if (token !== this.detailGeneration) return;
      let outcome = null;
      if (["completed", "failed"].includes(task.state)) outcome = await this.api.getAgentResult(project, id);
      if (token !== this.detailGeneration) return;
      this.renderTask(task, outcome, token);
    } catch (error) {
      if (token === this.detailGeneration) this.status("detail", this.errorText(error));
    }
  }
  renderTask(task, outcome, token) {
    const state = outcome ? outcome.state : task.state, project = this.project;
    const labels = {pending:"Waiting for an executor; this page does not start execution.", running:"Execution started; refresh for current state.",
      waiting_confirmation:"Waiting for archive confirmation; no files have been moved by this proposal.",
      waiting_execution:"Waiting for explicit archive execution; confirmation alone does not move files.",
      interrupted:"Interrupted; no automatic continuation.", cancelled:"Cancelled; already committed work is not undone.",
      completed:"Completed answer draft; not a saved handover report.", failed:"Failed; no automatic retry."};
    const archive = state === "completed" && outcome?.kind === "archive_operation";
    this.status("detail", `Task ${task.id} · ${state}. ${archive ? "Archive operation committed; this is not a model answer or a handover report." : labels[state] || "Unknown state; refresh."}`);
    const box = this.get("agent-detail");
    box.replaceChildren(this.node("p", `Created ${task.created_at} UTC · Updated ${task.updated_at} UTC`));
    for (const item of task.versions) box.append(this.node("p", `File ${item.file_id} · Version ${item.version_id}`));
    box.append(this.button("Refresh task", () => { if (token === this.detailGeneration) return this.view(task.id); }));
    box.append(this.button("Load / refresh tool calls", () => this.audit(project, task.id, token)));
    box.append(this.button("View archive plan", () => this.loadArchive(project, task.id, token)));
    box.append(this.button("Check archive receipt", () => this.loadArchive(project, task.id, token, true)));
    if (["pending", "running", "interrupted", "waiting_confirmation", "waiting_execution"].includes(state)) box.append(this.button("Cancel task", () => this.cancel(task.id, token)));
    if (outcome) box.append(this.node("p", `Admitted model requests: ${outcome.model_calls} · Reason: ${outcome.error_code || "none"}. These are not confirmed provider usage or cost.`));
    if (archive) {
      const receipt = outcome.archive_operation;
      box.append(this.node("p", `${receipt.moved_count} files archived · Operation ${receipt.operation_id} · Audit ${receipt.audit_id}. This is the committed receipt, not a claim about their current location.`));
      return;
    }
    if (state !== "completed" || !outcome.result) return;
    box.append(this.node("pre", outcome.result.answer));
    if (!outcome.result.citations.length) box.append(this.node("p", "No citations supplied; do not treat this as verified evidence."));
    for (const ref of outcome.result.citations) {
      const card = this.node("article"); card.className = "task-card";
      card.append(this.node("p", `File ${ref.file_id} · Version ${ref.version_id} · Chunk ${ref.chunk_id}`),
        this.button("Read cited source", () => this.source(project, task.id, ref, token)));
      box.append(card);
    }
  }
  clearArchive() {
    this.archiveGeneration += 1;
    this.get("agent-archive").replaceChildren();
    this.status("archive", "Load a plan to review a specific revision, or check its committed receipt.");
  }
  async loadArchive(project, id, token, receipt = false) {
    if (token !== this.detailGeneration || this.busy || this.creating || this.archiveWriting) return;
    this.clearArchive(); const request = this.archiveGeneration;
    const current = () => token === this.detailGeneration && request === this.archiveGeneration;
    this.status("archive", "Checking access…");
    try {
      if (receipt) {
        const result = await this.api.getArchiveReceipt(project, id);
        if (current()) this.renderArchiveReceipt(result);
      } else {
        const plan = await this.api.getArchivePlan(project, id);
        if (!current()) return;
        // Directory names are local UI metadata, never added to model context here.
        const directories = await this.api.listDirectories(project);
        if (current()) this.renderArchive(plan, directories.items, project, id, token, request);
      }
    } catch (error) {
      if (current()) { this.clearArchive(); this.status("archive", `${this.errorText(error)} Refresh to recheck access and state.`); }
    }
  }
  renderArchiveReceipt(receipt) {
    this.get("agent-archive").replaceChildren(
      this.node("p", `Plan ${receipt.plan_id} · Revision ${receipt.revision} · ${receipt.status}`),
      this.node("p", `${receipt.moved_count} files archived · Operation ${receipt.operation_id} · Audit ${receipt.audit_id}. This is the committed receipt, not their current location.`));
    this.status("archive", "Committed archive receipt; no automatic execution or retry.");
  }
  renderArchive(plan, directories, project, id, token, request) {
    const box = this.get("agent-archive"), names = new Map(directories.map(item => [item.id, item.name]));
    box.replaceChildren(this.node("p", `Plan ${plan.id} · Revision ${plan.revision} · ${plan.state} · Task ${plan.task_state}`),
      this.node("p", `Revision digest: ${plan.content_sha256}`));
    for (const item of plan.items) {
      const card = this.node("article"); card.className = "task-card";
      card.append(this.node("p", `${item.name} · File ${item.file_id} · Version ${item.version_id}`),
        this.node("p", `From: ${names.get(item.source_directory_id) || "Directory unavailable"} · ${item.source_directory_id}`),
        this.node("p", item.target_directory_id ? `To: ${item.target_name} · ${item.target_directory_id}` : "Needs review: no approved target directory."),
        this.node("p", `Reason: ${item.reason}`));
      box.append(card);
    }
    this.status("archive", "Review every item. Confirmation alone does not move files; execution is a separate action.");
    if (!["editor", "admin"].includes(this.role)) return;
    const ready = () => token === this.detailGeneration && request === this.archiveGeneration &&
      !this.busy && !this.creating && !this.archiveWriting && ["editor", "admin"].includes(this.role);
    const write = async (execute, decision) => {
      if (!ready()) return;
      this.archiveWriting = true; this.clearArchive(); this.controls();
      const writing = this.archiveGeneration;
      const current = () => token === this.detailGeneration && writing === this.archiveGeneration;
      this.status("archive", execute ? "Requesting archive execution…" : "Saving your decision…");
      try {
        const result = execute ? await this.api.executeArchivePlan(project, id, {
          plan_id: plan.id, revision: plan.revision, operation_id: plan.operation_id
        }) : await this.api.decideArchivePlan(project, id, {
          plan_id: plan.id, revision: plan.revision, content_sha256: plan.content_sha256, decision
        });
        if (!current()) return;
        if (execute) {
          this.renderArchiveReceipt(result);
          this.status("detail", `Task ${id} · Archive committed. Refresh task to load its current state.`);
        } else this.renderArchive(result, directories, project, id, token, writing);
      } catch (error) {
        if (current()) {
          this.clearArchive();
          this.status("archive", `${this.errorText(error)} Outcome may be unknown. Check archive receipt or refresh the plan before another explicit action; no automatic retry.`);
        }
      } finally { this.archiveWriting = false; this.controls(); }
    };
    const actions = this.node("div"); actions.className = "action-row";
    if (plan.state === "preview" && plan.task_state === "waiting_confirmation") {
      if (!plan.has_unresolved) {
        const checkbox = this.node("input"); checkbox.type = "checkbox"; checkbox.checked = false;
        const label = this.node("label"); label.className = "checkbox-label";
        label.append(checkbox, this.node("span", `I approve these exact items in revision ${plan.revision}.`));
        const confirm = this.button("Confirm this revision", () => { if (checkbox.checked) return write(false, "confirm"); });
        confirm.disabled = true;
        checkbox.addEventListener("change", () => { confirm.disabled = !checkbox.checked; });
        box.append(label); actions.append(confirm);
      }
      actions.append(this.button("Reject this revision", () => write(false, "reject")));
    } else if (plan.state === "confirmed" && plan.task_state === "waiting_execution" && plan.executable) {
      actions.append(this.button("Execute confirmed revision", () => write(true)));
    } else if (plan.state === "rejected") box.append(this.node("p", "Rejected. A new revision is required before confirmation."));
    box.append(actions);
  }
  async audit(project, id, token) {
    if (token !== this.detailGeneration || this.busy || this.creating || this.archiveWriting) return;
    const request = (this.auditGeneration || 0) + 1; this.auditGeneration = request;
    const current = () => token === this.detailGeneration && request === this.auditGeneration;
    const box = this.get("agent-audit"); box.replaceChildren();
    this.status("audit", "Checking access and loading backend requests…");
    try {
      const trace = await this.api.getAgentToolCalls(project, id);
      if (!current()) return;
      if (trace.kind !== "backend_requests" || trace.capacity !== 64 || !Array.isArray(trace.items) || trace.items.length > 64)
        throw new Error("Tool-call summary is unavailable.");
      const states = {success:"Request succeeded", processing:"Processing; content not ready", unavailable:"Content unavailable",
        invalid_request:"Invalid request", access_denied:"Access denied", conflict:"Conflict", busy:"Busy", timeout:"Timed out",
        dependency_failed:"Dependency failed", unknown:"Outcome unknown (not confirmed)"};
      for (const item of trace.items) {
        const card = this.node("details"); card.className = "task-card";
        const name = item.tool === "search_documents" ? "Search documents" :
          item.tool === "read_document_version" ? "Read document version" : "Unknown tool";
        const duration = Number.isSafeInteger(item.duration_ms) && item.duration_ms >= 0 ? `${item.duration_ms} ms` : "not recorded";
        const parameters = item.parameters || {}, summary = [];
        if (item.tool === "search_documents") {
          if (["metadata", "keyword", "hybrid"].includes(parameters.mode)) summary.push(`mode: ${parameters.mode}`);
          if (Number.isSafeInteger(parameters.query_bytes) && parameters.query_bytes >= 0) summary.push(`query bytes: ${parameters.query_bytes}`);
        } else if (item.tool === "read_document_version" && ["status", "chunk", "range"].includes(parameters.selector))
          summary.push(`selector: ${parameters.selector}`);
        card.append(this.node("summary", `${item.sequence}. ${name} · ${states[item.status] || states.unknown} · ${duration}`),
          this.node("p", `Tool: ${item.tool} · Request: ${item.request_id}`),
          this.node("p", `Started ${item.started_at} UTC · Backend duration: ${duration}`),
          this.node("p", `Parameters (redacted): ${summary.join(" · ") || "not recorded"}`));
        box.append(card);
      }
      this.status("audit", `${trace.items.length ? `${trace.items.length} backend requests / 64 capacity.` : "No recorded backend requests; this does not prove task completion or absence of model calls."} ` +
        "Snapshot only; these are backend requests, not MCP or model call counts. Duration excludes model generation; no token usage or cost is available here.");
    } catch (error) {
      if (current()) { this.clearDetail(); this.status("detail", `${this.errorText(error)} Refresh task to recheck access.`); }
    }
  }
  async cancel(id, token) {
    if (token !== this.detailGeneration || this.busy || this.creating || this.archiveWriting) return;
    const project = this.project, generation = this.generation;
    this.busy = true; this.controls(); this.clearDetail();
    this.status("detail", "Requesting cancellation…");
    try {
      await this.api.cancelAgentTask(project, id);
      if (generation !== this.generation) return;
      this.busy = false; await this.view(id);
    } catch (error) {
      if (generation === this.generation) this.status("detail", `${this.errorText(error)} Refresh tasks to check actual state; no automatic retry.`);
    } finally {
      this.busy = false; this.controls();
    }
  }
  async source(project, id, ref, token) {
    if (token !== this.detailGeneration || this.busy || this.creating || this.archiveWriting) return;
    const sourceToken = (this.sourceGeneration || 0) + 1; this.sourceGeneration = sourceToken;
    const current = () => token === this.detailGeneration && sourceToken === this.sourceGeneration;
    const box = this.get("agent-source"); box.replaceChildren();
    try {
      // Recheck the whole answer dependency set, not just this citation.
      const outcome = await this.api.getAgentResult(project, id);
      if (!current()) return;
      if (outcome.state !== "completed" || !outcome.result.citations.some(item =>
        item.file_id === ref.file_id && item.version_id === ref.version_id && item.chunk_id === ref.chunk_id)) throw new Error("Answer is no longer available.");
      const source = await this.api.readDocument(project, ref.file_id, ref.version_id, ref.chunk_id);
      if (!current()) return;
      const chunk = source.chunk;
      box.append(this.node("p", source.is_current ? "Current version at access check." : "Historical version; not redirected to the latest version."),
        this.node("p", `File ${ref.file_id} · Version ${ref.version_id} · Chunk ${ref.chunk_id} · ` +
          `${chunk.page ? `PDF physical page ${chunk.page} (starts at 1; may differ from printed pages) · ` : ""}Lines ${chunk.line_start}–${chunk.line_end}`),
        this.node("pre", chunk.text));
      const link = this.node("a", "Open exact original version");
      link.href = this.api.versionContentUrl(project, ref.file_id, ref.version_id) + (chunk.page ? `#page=${chunk.page}` : "");
      link.target = "_blank"; link.rel = "noopener"; box.append(link);
    } catch (error) {
      if (current()) { this.clearDetail(); this.status("detail", `${this.errorText(error)} Refresh task to recheck access.`); }
    }
  }
}
