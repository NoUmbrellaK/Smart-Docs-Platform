// User-driven snapshots. Creating a task is not starting a model request.
export class AgentPanel {
  constructor(get, api, errorText) {
    Object.assign(this, {get, api, errorText});
    this.generation = 0;
    this.listGeneration = 0;
    this.detailGeneration = 0;
    this.creating = false;
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
    const blocked = !this.project || this.busy || this.creating;
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
    if (this.creating) return;
    this.uncertain = false;
    this.clearDraft();
    this.status("create", "Select approved current versions in File detail. A new draft does not retry an earlier task.");
    this.controls();
  }
  setProject(project) {
    this.project = project;
    this.invalidate();
  }
  invalidate() {
    this.generation += 1;
    this.listGeneration += 1;
    this.detailGeneration += 1;
    this.busy = false;
    this.next = null;
    this.clearDraft();
    this.status("create", this.creating ? "A create request is still in flight; wait and check your tasks." :
      this.uncertain ? "An earlier creation outcome may be unknown. Check your tasks before explicitly starting a new draft." :
      "Select approved current versions in File detail.");
    this.controls();
    for (const id of ["list", "detail", "source"]) this.get(`agent-${id}`).replaceChildren();
    this.status("list", "Refresh to load your tasks.");
    this.status("detail", "Select a task to check its current state.");
  }
  addVersion(project, file, version) {
    if (!this.project || project !== this.project || this.busy || this.creating) return;
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
          if (generation !== this.generation || this.busy || this.creating) return;
          this.scope.delete(item.file_id); this.get("agent-consent").checked = false; this.renderScope();
        }));
      return card;
    }));
  }
  async create() {
    if (!this.project || this.busy || this.creating || this.uncertain) return;
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
    this.get("agent-detail").replaceChildren(); this.get("agent-source").replaceChildren();
    this.status("detail", "Select a task to check its current state.");
  }
  async refresh(before = "") {
    if (!this.project || this.busy || this.creating) return;
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
    if (!this.project || this.busy || this.creating) return;
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
      interrupted:"Interrupted; no automatic continuation.", cancelled:"Cancelled; already committed work is not undone.",
      completed:"Completed answer draft; not a saved handover report.", failed:"Failed; no automatic retry."};
    this.status("detail", `Task ${task.id} · ${state}. ${labels[state] || "Unknown state; refresh."}`);
    const box = this.get("agent-detail");
    box.replaceChildren(this.node("p", `Created ${task.created_at} UTC · Updated ${task.updated_at} UTC`));
    for (const item of task.versions) box.append(this.node("p", `File ${item.file_id} · Version ${item.version_id}`));
    box.append(this.button("Refresh task", () => { if (token === this.detailGeneration) return this.view(task.id); }));
    if (["pending", "running", "interrupted"].includes(state)) box.append(this.button("Cancel task", () => this.cancel(task.id, token)));
    if (outcome) box.append(this.node("p", `Admitted model requests: ${outcome.model_calls} · Reason: ${outcome.error_code || "none"}. Tool-call audit is not available in this page yet.`));
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
  async cancel(id, token) {
    if (token !== this.detailGeneration || this.busy || this.creating) return;
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
      if (generation === this.generation) { this.busy = false; this.controls(); }
    }
  }
  async source(project, id, ref, token) {
    if (token !== this.detailGeneration || this.busy || this.creating) return;
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
