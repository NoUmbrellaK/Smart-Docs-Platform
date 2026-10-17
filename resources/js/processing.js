// User-driven snapshots only: no polling, automatic retry or client-side authority.
export class ProcessingPanel {
  constructor(get, api, errorText, canWrite, changed) {
    Object.assign(this, { get, api, errorText, canWrite, changed });
    this.generation = 0;
    get("processing-refresh").addEventListener("click", () => this.refresh());
    this.reset();
  }

  node(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  reset() {
    this.generation += 1;
    this.selection = null;
    this.busy = false;
    this.get("processing-refresh").disabled = true;
    this.get("processing-status").textContent = "Choose a version to inspect processing.";
    this.get("processing-jobs").replaceChildren();
  }

  select(project, file, version) {
    this.reset();
    this.selection = { project, file, version };
    return this.refresh();
  }

  async refresh() {
    if (!this.selection || this.busy) return;
    const token = ++this.generation;
    const { project, file, version } = this.selection;
    this.get("processing-jobs").replaceChildren();
    this.get("processing-refresh").disabled = true;
    this.get("processing-status").textContent = "Loading processing status…";
    try {
      const data = await this.api.readDocument(project, file, version);
      if (token !== this.generation) return;
      const jobs = [];
      for (const id of [data.parse_job_id, data.vector_job_id]) {
        if (!id) continue;
        jobs.push(await this.api.getProcessingJob(project, id));
        if (token !== this.generation) return;
      }
      this.get("processing-status").textContent =
        `Version ${version} · ${data.is_current ? "Current version at check." : "Historical version; read-only."} ` +
        `Parse: ${data.parse_state} · Keyword: ${data.keyword_state} · Vector: ${data.vector_state}. ` +
        "Snapshot only; Refresh to check progress. Upload complete does not mean indexed.";
      this.buttons = [];
      this.get("processing-jobs").replaceChildren(...jobs.map(job => {
        const card = this.node("article");
        card.className = "task-card";
        card.append(this.node("h4", job.task_type === "vectorize" ? "Semantic index" : "Text extraction and keywords"),
          this.node("p", `Task ${job.id} · State: ${job.state} · Attempts: ${job.attempt_count}`));
        if (job.error_code) card.append(this.node("p", `Reason: ${job.error_code}`));
        const action = ["pending", "running"].includes(job.state) ? "cancel" :
          ["failed", "interrupted", "cancelled"].includes(job.state) ? "retry" : null;
        if (action && data.is_current && this.canWrite()) {
          const button = this.node("button", action === "retry" ? "Retry" : "Cancel");
          button.type = "button";
          button.addEventListener("click", () => this.act(token, job, action));
          this.buttons.push(button);
          card.append(button);
        }
        return card;
      }));
      if (!jobs.length) this.get("processing-jobs").append(this.node("p", "No processing task is recorded for this version."));
    } catch (error) {
      if (token === this.generation) this.get("processing-status").textContent = this.errorText(error);
    } finally {
      if (token === this.generation) this.get("processing-refresh").disabled = false;
    }
  }

  async act(token, job, action) {
    if (token !== this.generation || this.busy || !this.canWrite()) return;
    const { project } = this.selection;
    this.busy = true;
    this.get("processing-refresh").disabled = true;
    for (const button of this.buttons) button.disabled = true;
    this.get("processing-status").textContent = "Requesting task change…";
    this.changed(project);
    try {
      const operation = action === "retry" ? this.api.retryProcessingJob : this.api.cancelProcessingJob;
      await operation(project, job.id);
      this.changed(project);
      if (token !== this.generation) return;
      this.busy = false;
      await this.refresh();
    } catch (error) {
      this.changed(project);
      if (token !== this.generation) return;
      this.generation += 1; // A lost response is not proof that the mutation failed.
      this.busy = false;
      this.get("processing-jobs").replaceChildren();
      this.get("processing-status").textContent = `${this.errorText(error)} Refresh to check actual state before retrying.`;
      this.get("processing-refresh").disabled = false;
    }
  }
}
