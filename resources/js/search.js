// Local user search only: these results are not an Agent/remote-AI context.
export class SearchPanel {
  constructor(get, api, errorText) {
    this.get = get;
    this.api = api;
    this.errorText = errorText;
    this.generation = 0;
    this.sourceGeneration = 0;
    this.project = null;
    get("document-search-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const criteria = { mode: get("document-search-mode").value,
        query: get("document-search-query").value, page_size: 10 };
      for (const [field, control] of [["directory_id", "directory"], ["media_type", "media"]]) {
        const value = get(`document-search-${control}`).value;
        if (value) criteria[field] = value;
      }
      return this.search(criteria, 1);
    });
    for (const control of ["mode", "query", "directory", "media"]) {
      get(`document-search-${control}`).addEventListener("input", () => this.invalidate());
    }
    for (const [control, field] of [["previous", "previous"], ["next", "next"]]) {
      get(`document-search-${control}`).addEventListener("click", () => {
        if (this[field]) return this.search(this.criteria, this[field]);
      });
    }
  }

  node(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  setProject(project, directories) {
    this.project = project;
    this.invalidate();
    const options = [this.node("option", "All directories")];
    options[0].value = "";
    for (const directory of directories) {
      const option = this.node("option", directory.name);
      option.value = directory.id;
      options.push(option);
    }
    this.get("document-search-directory").replaceChildren(...options);
    this.get("document-search-directory").value = "";
    this.get("document-search-submit").disabled = !project;
  }

  invalidate() {
    this.generation += 1;
    this.sourceGeneration += 1;
    this.previous = this.next = null;
    for (const id of ["results", "source"]) this.get(`document-search-${id}`).replaceChildren();
    this.get("document-search-status").textContent = "Search to load current results.";
    this.get("document-source-status").textContent = "";
    this.get("document-search-previous").disabled = true;
    this.get("document-search-next").disabled = true;
  }

  async search(criteria, page) {
    if (!this.project) return;
    this.invalidate();
    const generation = this.generation;
    const project = this.project;
    const status = this.get("document-search-status");
    if (new TextEncoder().encode(criteria.query).length > 256) {
      status.textContent = "Query must be at most 256 UTF-8 bytes.";
      return;
    }
    if (criteria.mode !== "metadata" && !criteria.query.trim()) {
      status.textContent = "Enter a keyword; file-name search may be empty.";
      return;
    }
    status.textContent = criteria.mode === "hybrid" ? "Searching approved current versions… Local encoding may take a minute." : "Searching…";
    try {
      const response = await this.api.searchDocuments(project, { ...criteria, page });
      if (generation !== this.generation) return;
      this.criteria = criteria;
      this.previous = response.page > 1 ? response.page - 1 : null;
      this.next = response.next_page;
      this.get("document-search-previous").disabled = !this.previous;
      this.get("document-search-next").disabled = !this.next;
      const coverage = response.coverage;
      status.textContent = `Page ${response.page} · ${response.items.length} results · ${response.retrieval}. ` +
        (criteria.mode === "hybrid" ? "Approved current versions only. " +
          (response.retrieval !== "hybrid" ? "Vector search unavailable; degraded to keywords within the same approved scope. " : "") : "") +
        `Scope snapshot (not a match count): ${coverage.current_files} files, ` +
        `${coverage.keyword_ready_files} keyword ready, ${coverage.unsearched_files} unindexed. ` +
        (coverage.vector_completed_files !== undefined ? `${coverage.vector_completed_files} vector completed (not a recall guarantee). ` : "") +
        `${response.truncated ? "Partial result page. " : ""}` +
        `Warnings: ${response.warnings.join(", ") || "none"}. ` +
        "Live pages can shift, repeat or omit results. No matches does not prove absence.";
      this.get("document-search-results").replaceChildren(...response.items.map((item) => {
        const card = this.node("article");
        card.className = "search-card";
        card.append(this.node("h3", item.name), this.node("p",
          `File ${item.file_id} · Version ${item.version_id}`), this.node("p",
          `Parse: ${item.parse_state} · Keyword: ${item.keyword_state} · Vector: ${item.vector_state} · ${item.availability_state}`));
        if (item.chunk) card.append(this.node("p", item.chunk.text));
        if (item.match_sources) card.append(this.node("p", `Matched by: ${item.match_sources.join(", ")}`));
        const open = this.node("button", "Read fixed-version source");
        open.type = "button";
        open.addEventListener("click", () => this.source(project, item, generation));
        card.append(open);
        return card;
      }));
    } catch (error) {
      if (generation === this.generation) status.textContent = this.errorText(error);
    }
  }

  async source(project, item, generation) {
    if (generation !== this.generation) return;
    const sourceGeneration = ++this.sourceGeneration;
    const current = () => generation === this.generation && sourceGeneration === this.sourceGeneration;
    const container = this.get("document-search-source");
    const status = this.get("document-source-status");
    container.replaceChildren();
    status.textContent = "Checking source access…";
    try {
      const source = await this.api.readDocument(project, item.file_id, item.version_id,
        item.chunk && item.chunk.chunk_id);
      if (!current()) return;
      status.textContent = source.is_current ? "Current version at access check." :
        "This is a historical version; it has not been redirected to the latest version.";
      container.append(this.node("p", `File ${source.file_id} · Version ${source.version_id}`),
        this.node("p", `Parse: ${source.parse_state} · Keyword: ${source.keyword_state} · Vector: ${source.vector_state}`));
      if (source.chunk) {
        const chunk = source.chunk;
        container.append(this.node("p", `Chunk ${chunk.chunk_id} · ` +
          `${chunk.page ? `PDF page ${chunk.page} · ` : ""}Lines ${chunk.line_start}–${chunk.line_end}`),
        this.node("pre", chunk.text));
      } else {
        container.append(this.node("p", "Document status only; use keyword search for extracted passages or open the original."));
      }
      if (source.pages_without_text.length) container.append(this.node("p",
        `Pages without extracted text: ${source.pages_without_text.join(", ")}. No OCR was performed.`));
      const link = this.node("a", "Open exact original version");
      link.href = this.api.versionContentUrl(project, item.file_id, item.version_id) +
        (source.chunk && source.chunk.page ? `#page=${source.chunk.page}` : "");
      link.target = "_blank";
      link.rel = "noopener";
      container.append(link);
    } catch (error) {
      if (current()) status.textContent = this.errorText(error);
    }
  }
}
