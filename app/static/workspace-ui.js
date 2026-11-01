/* Library operations, reusable settings, resumable transfer and measured comparison. */
(() => {
  const state = {
    profiles: [],
    vocabulary: [],
    projects: [],
    settings: {},
    selected: new Set(),
    libraryProject: "",
    libraryTag: "",
    archive: "active",
    lastWorkspace: 0,
    queue: null,
  };
  const el = (tag, cls, text) => node(tag, cls || "", text),
    get = (id) => document.getElementById(id);
  const request = (path, method, body) =>
    api(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  const button = (label, fn, cls = "secondary") => {
    const b = el("button", cls, label);
    b.type = "button";
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await fn();
      } catch (error) {
        const container = b.closest("dialog");
        if (container) {
          let message = container.querySelector(".workspace-error");
          if (!message) {
            message = el("p", "workspace-error");
            message.setAttribute("role", "alert");
            container.append(message);
          }
          message.textContent = error.message;
        } else feedback(error.message, true);
      } finally {
        b.disabled = false;
      }
    });
    return b;
  };
  const option = (value, label) => {
    const o = el("option", "", label);
    o.value = value;
    return o;
  };
  function field(label, input) {
    const holder = el("label", "workspace-field", label);
    holder.append(input);
    return holder;
  }
  function dialog(title) {
    const d = el("dialog", "workspace-dialog");
    const header = el("div", "dialog-heading");
    header.append(
      el("h2", "", title),
      button("×", () => d.close(), "icon-button"),
    );
    d.append(header);
    document.body.append(d);
    d.addEventListener("close", () => d.remove());
    d.addEventListener("click", (e) => {
      if (e.target === d) {
        const b = d.getBoundingClientRect();
        if (
          e.clientX < b.left ||
          e.clientX > b.right ||
          e.clientY < b.top ||
          e.clientY > b.bottom
        )
          d.close();
      }
    });
    return d;
  }
  async function download(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let message = "Export failed";
      try {
        message = (await response.json()).detail || message;
      } catch {}
      throw Error(message);
    }
    const blob = await response.blob(),
      a = el("a");
    const disposition = response.headers.get("Content-Disposition") || "";
    let filename = "transcripts.zip";
    const encoded = /filename\*=utf-8''([^;]+)/i.exec(disposition),
      plain = /filename="([^"]+)"/.exec(disposition);
    if (encoded) filename = decodeURIComponent(encoded[1]);
    else if (plain) filename = plain[1];
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  }
  function section(title, caption) {
    const s = el("section", "settings-card workspace-card");
    s.append(el("h2", "", title));
    if (caption) s.append(el("p", "quiet", caption));
    return s;
  }
  function settingsValues() {
    return {
      ...state.settings,
      parallel_jobs: Number(
        get("parallelLevel").value === "custom"
          ? get("customParallel").value
          : get("parallelLevel").value,
      ),
      segment_seconds: Number(get("defaultChunk").value),
      input_language: get("defaultInputLanguage").value,
      output_language: get("defaultOutputLanguage").value,
      diarize: get("defaultDiarize").checked,
      number_style: get("defaultNumberStyle").value,
      guidance: get("defaultGuidance").value,
      temperature: Number(get("modelTemperature").value),
      seed:
        get("modelSeed").value === "" ? null : Number(get("modelSeed").value),
      chunk_mode: get("chunkMode").value,
      overlap_seconds: Number(get("chunkOverlap").value),
      channel_mode: get("channelMode").value,
      vocabulary_ids: [...get("activeVocabulary").selectedOptions].map(
        (o) => o.value,
      ),
    };
  }
  function hydrate(settings) {
    state.settings = settings;
    for (const [id, key] of [
      ["chunkMode", "chunk_mode"],
      ["chunkOverlap", "overlap_seconds"],
      ["channelMode", "channel_mode"],
    ])
      if (get(id))
        get(id).value =
          settings[key] ??
          { chunk_mode: "fixed", overlap_seconds: 0, channel_mode: "mix" }[key];
    if (get("activeVocabulary"))
      for (const opt of get("activeVocabulary").options)
        opt.selected = (settings.vocabulary_ids || []).includes(opt.value);
  }
  async function workspace() {
    const data = await api("/api/workspace");
    Object.assign(state, {
      profiles: data.profiles || [],
      vocabulary: data.vocabulary || [],
      projects: data.projects || [],
      queue: data.queue,
    });
    state.lastWorkspace = Date.now();
    renderLists();
    showQueue();
    return data;
  }
  function populate(select, items, empty) {
    const selected = select.value;
    select.replaceChildren(
      option("", empty),
      ...items.map((item) => option(item.id, item.name)),
    );
    if ([...select.options].some((o) => o.value === selected))
      select.value = selected;
  }
  function renderLists() {
    for (const id of ["uploadProfile", "savedProfile"])
      if (get(id)) populate(get(id), state.profiles, "Current defaults");
    if (get("libraryProject"))
      populate(get("libraryProject"), state.projects, "All projects");
    if (get("activeVocabulary")) {
      const selected = state.vocabularyReady
        ? [...get("activeVocabulary").selectedOptions].map((o) => o.value)
        : state.settings.vocabulary_ids || [];
      get("activeVocabulary").replaceChildren(
        ...state.vocabulary.map((item) => option(item.id, item.name)),
      );
      for (const opt of get("activeVocabulary").options)
        opt.selected = selected.includes(opt.value);
      state.vocabularyReady = true;
    }
    if (get("editVocabulary"))
      populate(get("editVocabulary"), state.vocabulary, "New vocabulary list");
  }
  function mountSettings() {
    const form = get("settingsForm");
    const processing = section(
      "Audio preparation",
      "Choose how recordings are divided and how audio channels are treated. New uploads use these settings.",
    );
    processing.innerHTML +=
      '<div class="setting-row"><label for="chunkMode">Chunk boundaries<small>Silence-aware mode looks for a nearby pause while retaining all audio.</small></label><select id="chunkMode"><option value="fixed">Fixed length</option><option value="silence">Near pauses</option></select></div><div class="setting-row"><label for="chunkOverlap">Context overlap<small>Additional audio around boundaries. Matched text is reconciled against original timing.</small></label><input id="chunkOverlap" type="number" min="0" max="10" step="0.5" value="0" /></div><div class="setting-row"><label for="channelMode">Audio channels<small>Separate channels is useful for interviews recorded with one voice per channel.</small></label><select id="channelMode"><option value="mix">Mix to mono</option><option value="separate">Transcribe separately</option></select></div>';
    const profiles = section(
      "Transcription profiles",
      "Save a named configuration, then apply it from Settings or Upload.",
    );
    const profile = el("select");
    profile.id = "savedProfile";
    const profileName = el("input");
    profileName.id = "profileName";
    profileName.placeholder = "e.g. Romanian interview";
    profileName.maxLength = 120;
    profiles.append(
      field("Saved profile", profile),
      field("Profile name", profileName),
    );
    const actions = el("div", "workspace-actions");
    actions.append(
      button("Save profile", async () => {
        const id = profile.value;
        await request(
          id ? "/api/profiles/" + id : "/api/profiles",
          id ? "PUT" : "POST",
          { name: profileName.value, settings: settingsValues() },
        );
        await workspace();
        feedback("Profile saved.");
      }),
      button("Apply", async () => {
        if (!profile.value) return;
        await request("/api/profiles/" + profile.value + "/apply", "POST", {});
        await loadSettings();
        feedback("Profile applied to new recordings.");
      }),
      button("Delete", async () => {
        if (!profile.value) return;
        await api("/api/profiles/" + profile.value, { method: "DELETE" });
        profileName.value = "";
        await workspace();
      }),
    );
    profiles.append(actions);
    profile.addEventListener("change", () => {
      profileName.value =
        state.profiles.find((p) => p.id === profile.value)?.name || "";
    });
    const vocabulary = section(
      "Vocabulary",
      "Keep spellings for names and terminology. Selected lists become contextual guidance, not automatic substitutions.",
    );
    const active = el("select");
    active.id = "activeVocabulary";
    active.multiple = true;
    active.size = 3;
    vocabulary.append(
      field("Lists used by default (Ctrl / ⌘ to select several)", active),
    );
    const edit = el("details", "workspace-details");
    edit.append(el("summary", "", "Manage lists"));
    const picker = el("select");
    picker.id = "editVocabulary";
    const name = el("input");
    name.id = "vocabularyName";
    name.placeholder = "List name";
    name.maxLength = 120;
    const terms = el("textarea");
    terms.id = "vocabularyTerms";
    terms.rows = 5;
    terms.placeholder = "One name or term per line";
    edit.append(
      field("List", picker),
      field("Name", name),
      field("Terms", terms),
    );
    const controls = el("div", "workspace-actions");
    controls.append(
      button("Save list", async () => {
        const id = picker.value;
        await request(
          id ? "/api/vocabulary/" + id : "/api/vocabulary",
          id ? "PUT" : "POST",
          {
            name: name.value,
            terms: terms.value
              .split("\n")
              .map((t) => t.trim())
              .filter(Boolean),
          },
        );
        await workspace();
        feedback(
          "Vocabulary list saved. Select it above and save settings to use it.",
        );
      }),
      button("Delete list", async () => {
        if (!picker.value) return;
        await api("/api/vocabulary/" + picker.value, { method: "DELETE" });
        name.value = "";
        terms.value = "";
        await workspace();
      }),
    );
    edit.append(controls);
    picker.addEventListener("change", () => {
      const value = state.vocabulary.find((v) => v.id === picker.value);
      name.value = value?.name || "";
      terms.value = (value?.terms || []).join("\n");
    });
    vocabulary.append(edit);
    for (const card of [processing, profiles, vocabulary])
      form.insertBefore(card, form.querySelector(".settings-save"));
    const upload = el("label", "upload-profile", "Profile");
    const uploadSelect = el("select");
    uploadSelect.id = "uploadProfile";
    upload.append(uploadSelect);
    get("drop").before(upload);
    uploadSelect.addEventListener("change", () => {
      const profile = state.profiles.find((p) => p.id === uploadSelect.value);
      const s = profile?.settings || state.settings;
      for (const [id, key] of [
        ["language", "input_language"],
        ["outputLanguage", "output_language"],
        ["numberStyle", "number_style"],
        ["duration", "segment_seconds"],
        ["uploadGuidance", "guidance"],
      ])
        get(id).value = s[key] ?? get(id).value;
      get("diarize").checked = !!s.diarize;
      speakerAvailability();
    });
  }
  function mountFiles() {
    const filters = el("div", "library-toolbar");
    filters.innerHTML =
      '<label>Project<select id="libraryProject"><option value="">All projects</option></select></label><label>Label<input id="libraryLabel" placeholder="Filter by label" maxlength="80" /></label><label>Show<select id="libraryArchive"><option value="active">Active files</option><option value="archived">Archived files</option><option value="all">All files</option></select></label><span id="queueETA" class="library-eta"></span>';
    get("queueNotice").after(filters);
    let timer;
    for (const [id, key] of [
      ["libraryProject", "libraryProject"],
      ["libraryArchive", "archive"],
      ["libraryLabel", "libraryTag"],
    ])
      get(id).addEventListener(
        id === "libraryLabel" ? "input" : "change",
        () => {
          state[key] = get(id).value;
          ui.filePage = 1;
          clearTimeout(timer);
          timer = setTimeout(refresh, id === "libraryLabel" ? 300 : 0);
        },
      );
    const bulk = el("div", "bulk-toolbar");
    bulk.id = "bulkToolbar";
    bulk.innerHTML =
      '<label class="bulk-select"><input id="selectPage" type="checkbox" /> Select page</label><span id="selectedCount">0 selected</span><select id="bulkAction" aria-label="Action for selected files"><option value="retry">Retry</option><option value="retry_current">Retry with current settings</option><option value="pause">Pause</option><option value="resume">Resume</option><option value="cancel">Cancel processing</option><option value="archive">Archive</option><option value="unarchive">Unarchive</option><option value="delete">Delete</option></select>';
    bulk.append(
      button("Apply", () => bulkAction(get("bulkAction").value)),
      button("Organize", () => organize([...state.selected])),
      button("Export", () => exportDialog([...state.selected])),
      button("Clear", () => {
        state.selected.clear();
        renderJobs();
        selectionCount();
      }),
    );
    filters.after(bulk);
    get("selectPage").addEventListener("change", (event) => {
      for (const job of ui.jobs)
        event.target.checked
          ? state.selected.add(job.id)
          : state.selected.delete(job.id);
      renderJobs();
      selectionCount();
    });
    selectionCount();
  }
  function selectionCount() {
    get("selectedCount").textContent = `${state.selected.size} selected`;
    get("bulkToolbar").classList.toggle(
      "has-selection",
      state.selected.size > 0,
    );
    get("selectPage").checked =
      !!ui.jobs.length && ui.jobs.every((j) => state.selected.has(j.id));
  }
  async function bulkAction(action) {
    const ids = [...state.selected];
    if (!ids.length) {
      feedback("Select recordings first.");
      return;
    }
    if (action === "delete") {
      const d = dialog("Delete selected recordings?");
      d.append(
        el(
          "p",
          "quiet",
          `${ids.length} original recordings and their results will be deleted.`,
        ),
        button(
          "Delete recordings",
          async () => {
            d.close();
            await performBulk(action, ids);
          },
          "danger-button",
        ),
      );
      d.showModal();
      return;
    }
    await performBulk(action, ids);
  }
  async function performBulk(action, ids, extra = {}) {
    const data = await request("/api/jobs/bulk", "POST", {
      ids,
      action,
      ...extra,
    });
    const failures = data.errors || data.results?.filter((x) => !x.ok) || [];
    feedback(
      failures.length
        ? `${failures.length} recording(s) could not be updated. ${failures.map((e) => e.error || e.detail).join(" ")}`
        : "Selected recordings updated.",
      failures.length > 0,
    );
    state.selected.clear();
    await refresh();
    selectionCount();
  }
  function organize(ids) {
    if (!ids.length) {
      feedback("Select a recording first.");
      return;
    }
    const d = dialog("Organize recordings");
    const project = el("select");
    project.append(
      option("", "No project"),
      ...state.projects.map((p) => option(p.id, p.name)),
    );
    const newProject = el("input");
    newProject.placeholder = "Or create a project";
    newProject.maxLength = 120;
    const tags = el("input");
    tags.placeholder = "interview, finance";
    const selected = ui.jobs.find((j) => j.id === ids[0]);
    project.value = selected?.project || "";
    tags.value = (selected?.tags || []).join(", ");
    d.append(
      field("Project", project),
      field("New project", newProject),
      field("Labels, separated by commas", tags),
      button(
        "Save",
        async () => {
          let id = project.value;
          if (newProject.value.trim()) {
            const item = await request("/api/projects", "POST", {
              name: newProject.value,
            });
            id = item.id || item.project?.id;
            await workspace();
          }
          await performBulk("metadata", ids, {
            project: id,
            tags: tags.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          });
          d.close();
        },
        "primary",
      ),
    );
    d.showModal();
  }
  function exportDialog(ids) {
    if (!ids.length) {
      feedback("Select recordings first.");
      return;
    }
    const d = dialog("Export recordings");
    const format = el("select");
    for (const f of ["txt", "docx", "pdf", "vtt", "json"])
      format.append(
        option(f, f === "vtt" ? "WebVTT subtitles" : f.toUpperCase()),
      );
    const source = el("select");
    source.append(
      option("original", "Model transcript"),
      option("review", "Reviewed transcript"),
    );
    const speakers = el("input");
    speakers.type = "checkbox";
    d.append(
      field("Format", format),
      field("Text source", source),
      field("Separate text by speaker", speakers),
      el(
        "p",
        "quiet",
        "Subtitle exports require valid complete timing. Multiple files are packaged in a ZIP; any failures are explained inside.",
      ),
      button(
        "Download",
        async () => {
          if (ids.length === 1)
            await download(
              `/api/jobs/${ids[0]}/export/${format.value}?source=${source.value}&speakers=${speakers.checked}`,
            );
          else
            await download("/api/jobs/export", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                ids,
                format: format.value,
                source: source.value,
                speakers: speakers.checked,
              }),
            });
          d.close();
        },
        "primary",
      ),
    );
    d.showModal();
  }
  function decorateFile(card, job) {
    const check = el("input", "file-select");
    check.type = "checkbox";
    check.checked = state.selected.has(job.id);
    check.setAttribute("aria-label", "Select " + job.filename);
    check.addEventListener("change", () => {
      check.checked
        ? state.selected.add(job.id)
        : state.selected.delete(job.id);
      selectionCount();
    });
    card.querySelector(".job-head").prepend(check);
    const items = card.querySelector(".job-menu-items");
    if (!items) return;
    const add = (label, fn) =>
      items.append(
        button(
          label,
          async () => {
            card.querySelector(".job-menu").open = false;
            await fn();
          },
          "",
        ),
      );
    add("Organize", () => organize([job.id]));
    if (["queued", "preparing", "processing"].includes(job.status))
      add("Pause", async () => {
        await request(`/api/jobs/${job.id}/control`, "POST", {
          action: "pause",
        });
        await refresh();
      });
    if (job.status === "paused")
      add("Resume", async () => {
        await request(`/api/jobs/${job.id}/control`, "POST", {
          action: "resume",
        });
        await refresh();
      });
    if (["queued", "paused", "preparing", "processing"].includes(job.status))
      add("Cancel processing", async () => {
        await request(`/api/jobs/${job.id}/control`, "POST", {
          action: "cancel",
        });
        await refresh();
      });
    if (job.status === "done") {
      add("Export document…", () => exportDialog([job.id]));
      add("Compare runs…", () => compareDialog(job));
      add("Run with another profile…", () => variantDialog(job));
      add("Evaluate accuracy…", () => benchmarkDialog(job));
    }
    add(job.archived ? "Unarchive" : "Archive", async () => {
      await request(`/api/jobs/${job.id}/metadata`, "PATCH", {
        archived: !job.archived,
      });
      await refresh();
    });
    const meta = card.querySelector(".job-name-wrap");
    const labels = job.tags || [];
    const project = state.projects.find((p) => p.id === job.project);
    if (project || labels.length)
      meta.append(
        el(
          "p",
          "file-labels",
          [project?.name, ...labels].filter(Boolean).join(" · "),
        ),
      );
    const queue = state.queue?.files?.find((file) => file.id === job.id);
    if (queue && job.status === "queued")
      card.append(
        el(
          "p",
          "job-eta",
          `Estimated start in ${duration(queue.start_in_s)} · complete in ${duration(queue.finish_in_s)}`,
        ),
      );
    selectionCount();
  }
  function showQueue() {
    const target = get("queueETA");
    if (!target) return;
    const queue = state.queue;
    target.textContent =
      queue?.available && queue.remaining_s > 0
        ? `Queue: about ${duration(queue.remaining_s)} remaining`
        : "";
    target.title = queue?.reason || "";
  }
  async function updateQueue() {
    if (Date.now() - state.lastWorkspace < 5000) return;
    state.lastWorkspace = Date.now();
    try {
      await workspace();
    } catch {}
  }
  async function compareDialog(job) {
    const data = await api(`/api/jobs/${job.id}/runs`);
    const runs = [...(data.runs || data)].sort((a, b) =>
      a.created_at.localeCompare(b.created_at),
    );
    const d = dialog("Compare transcription runs");
    d.classList.add("comparison-dialog");
    const controls = el("div", "compare-controls"),
      left = el("select"),
      right = el("select");
    for (const run of runs) {
      const label = `${run.id === "current" ? "Original" : run.id.startsWith("current:") ? "Variant" : "Retained"} · ${fileDate(run.created_at)} · ${speed(run.metrics?.rtfx)}`;
      left.append(option(run.id, label));
      right.append(option(run.id, label));
    }
    right.selectedIndex = Math.max(0, runs.length - 1);
    controls.append(
      field("Earlier / left", left),
      field("Later / right", right),
    );
    const body = el("div", "comparison-body");
    d.append(controls, body);
    async function render() {
      const report = await api(
        `/api/jobs/${job.id}/compare?left=${encodeURIComponent(left.value)}&right=${encodeURIComponent(right.value)}`,
      );
      body.replaceChildren();
      const a = el("section"),
        b = el("section");
      a.append(
        el(
          "h3",
          "",
          `${duration(report.left.metrics.wall_s)} · ${speed(report.left.metrics.rtfx)}`,
        ),
      );
      b.append(
        el(
          "h3",
          "",
          `${duration(report.right.metrics.wall_s)} · ${speed(report.right.metrics.rtfx)}`,
        ),
      );
      const ta = el("div", "diff-text"),
        tb = el("div", "diff-text");
      for (const part of report.diff) {
        ta.append(
          el(
            "span",
            part.operation === "equal" ? "" : "diff-deleted",
            part.left,
          ),
        );
        tb.append(
          el(
            "span",
            part.operation === "equal" ? "" : "diff-added",
            part.right,
          ),
        );
      }
      a.append(ta);
      b.append(tb);
      body.append(a, b);
      if (runs.length < 2)
        body.before(
          el(
            "p",
            "quiet",
            "One completed run is available. Retry this recording to retain the previous run, or create a variant.",
          ),
        );
    }
    for (const select of [left, right])
      select.addEventListener("change", () =>
        render().catch((e) => feedback(e.message, true)),
      );
    d.showModal();
    await render();
  }
  function variantDialog(job) {
    const d = dialog("Create a comparison run");
    const profile = el("select");
    profile.append(
      option("", "Current defaults"),
      ...state.profiles.map((p) => option(p.id, p.name)),
    );
    d.append(
      field("Configuration", profile),
      el(
        "p",
        "quiet",
        "A separate recording entry is created with a safe copy of the original media. Existing results and review text remain available.",
      ),
      button(
        "Queue variant",
        async () => {
          const settings =
            state.profiles.find((p) => p.id === profile.value)?.settings ||
            state.settings;
          await request(`/api/jobs/${job.id}/variant`, "POST", { settings });
          d.close();
          await refresh();
          switchTab("files");
          feedback("Comparison run queued.");
        },
        "primary",
      ),
    );
    d.showModal();
  }
  function benchmarkDialog(job) {
    const d = dialog("Evaluate transcription accuracy");
    const ref = el("textarea");
    ref.rows = 10;
    ref.placeholder =
      "Paste a human-reviewed reference transcript covering the same recording.";
    const name = el("input");
    name.value = job.filename;
    const output = el("div", "benchmark-result");
    d.append(
      field("Evaluation name", name),
      field("Verified reference text", ref),
      el(
        "p",
        "quiet",
        "Use an independent reference, not the model transcript. Word and character error rates use normalized text; lower is better.",
      ),
      button(
        "Measure WER / CER",
        async () => {
          const data = await request("/api/benchmarks", "POST", {
            job_id: job.id,
            run_id: "current",
            reference: ref.value,
            name: name.value,
            reference_source: "human",
          });
          renderBenchmark(output, data.report || data);
          await renderBenchmarks();
        },
        "primary",
      ),
      output,
    );
    d.showModal();
  }
  function renderBenchmark(target, data) {
    target.replaceChildren();
    const scores = el("div", "benchmark-score");
    for (const [label, key] of [
      ["Word error rate", "wer"],
      ["Character error rate", "cer"],
    ]) {
      const metric = data[key],
        card = el("div");
      card.append(
        el("span", "", label),
        el(
          "strong",
          "",
          typeof metric?.rate === "number"
            ? `${(metric.rate * 100).toFixed(2)}%`
            : "—",
        ),
      );
      scores.append(card);
    }
    target.append(scores);
    const w = data.wer || {};
    target.append(
      el(
        "p",
        "benchmark-counts",
        `${w.reference_length ?? 0} reference words · ${w.substitutions ?? 0} substitutions · ${w.insertions ?? 0} insertions · ${w.deletions ?? 0} deletions`,
      ),
      el("p", "quiet", data.normalization || ""),
    );
    if (data.hypothesis_is_reference)
      target.append(
        el(
          "p",
          "job-recovery-note",
          "Reference and model transcript are identical. Confirm this reference was independently reviewed before interpreting the score.",
        ),
      );
  }
  async function renderBenchmarks() {
    const holder = get("benchmarkHistory");
    if (!holder) return;
    const result = await api("/api/benchmarks");
    holder.replaceChildren();
    const rows = result.benchmarks || result;
    if (!rows.length) {
      holder.append(
        el(
          "p",
          "quiet",
          "Evaluate a completed recording against a verified reference. Results will appear here.",
        ),
      );
      return;
    }
    for (const item of rows.slice(0, 20)) {
      const row = el("div", "benchmark-row");
      row.append(
        el("b", "", item.name || item.job_id),
        el("span", "", fileDate(item.created_at)),
        button("View results", () => {
          const d = dialog(item.name || "Accuracy result");
          const result = el("div");
          renderBenchmark(result, item.report || item);
          d.append(result);
          d.showModal();
        }),
      );
      holder.append(row);
    }
  }
  function mountBenchmarks() {
    const details = el("details", "workspace-lab");
    details.append(el("summary", "", "Accuracy lab & comparisons"));
    const content = el("div", "lab-content");
    content.append(
      el(
        "p",
        "quiet",
        "Compare saved runs and evaluate the exact same audio against human-reviewed text. Model output is not a reference.",
      ),
    );
    const picker = el("select");
    picker.id = "labRecording";
    const actions = el("div", "workspace-actions");
    actions.append(
      button("Evaluate accuracy", () => {
        const job = ui.reviewJobs?.find((j) => j.id === picker.value);
        if (job) benchmarkDialog(job);
      }),
      button("Compare runs", () => {
        const job = ui.reviewJobs?.find((j) => j.id === picker.value);
        if (job) compareDialog(job);
      }),
      button("New comparison run", () => {
        const job = ui.reviewJobs?.find((j) => j.id === picker.value);
        if (job) variantDialog(job);
      }),
    );
    content.append(field("Completed recording", picker), actions);
    const history = el("div");
    history.id = "benchmarkHistory";
    content.append(history);
    details.append(content);
    get("statsTab").append(details);
    details.addEventListener("toggle", () => {
      if (details.open) {
        populate(
          picker,
          (ui.reviewJobs || []).map((j) => ({ id: j.id, name: j.filename })),
          "Choose a recording",
        );
        renderBenchmarks().catch((e) => feedback(e.message, true));
      }
    });
  }
  function uploadSettings(settings) {
    const profile = state.profiles.find(
      (p) => p.id === get("uploadProfile").value,
    );
    const base = { ...(profile?.settings || state.settings) };
    return {
      ...base,
      input_language: settings.language,
      output_language: settings.output_language,
      number_style: settings.number_style,
      guidance: settings.guidance,
      segment_seconds: Number(settings.segment_seconds),
      diarize: settings.diarize === "true",
    };
  }
  async function upload(file, settings, position, total) {
    const key = `nemotron-upload:${file.name}:${file.size}:${file.lastModified}`;
    let id = localStorage.getItem(key),
      session;
    if (id)
      try {
        session = await api("/api/uploads/" + id);
        if (session.job_id) {
          localStorage.removeItem(key);
          return { id: session.job_id, status: "queued" };
        }
        if (!["uploading", "ready", "finalizing"].includes(session.status)) {
          await api("/api/uploads/" + id, { method: "DELETE" });
          id = null;
        }
      } catch {
        id = null;
      }
    if (!id) {
      session = await request("/api/uploads", "POST", {
        filename: file.name,
        size: file.size,
        settings: uploadSettings(settings),
        fingerprint: `${file.size}:${file.lastModified}`,
      });
      id = session.id;
      localStorage.setItem(key, id);
    }
    session = await api("/api/uploads/" + id);
    let offset = session.offset || 0;
    const chunkSize = 4 * 1024 * 1024;
    while (offset < file.size) {
      let sent = false;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const result = await sendChunk(
            id,
            offset,
            file.slice(offset, Math.min(offset + chunkSize, file.size)),
            file,
            position,
            total,
          );
          offset = result.offset;
          sent = true;
          break;
        } catch (error) {
          if (attempt === 2)
            throw Error(
              `${error.message}. Reselect the same file to resume its upload.`,
            );
          await new Promise((resolve) =>
            setTimeout(resolve, 500 * (attempt + 1)),
          );
          const resumed = await api("/api/uploads/" + id);
          offset = resumed.offset;
        }
      }
      if (!sent)
        throw Error("Upload interrupted; reselect the same file to resume.");
    }
    get("uploadLabel").textContent = "Checking audio…";
    const completed = await request(
      "/api/uploads/" + id + "/complete",
      "POST",
      {},
    );
    localStorage.removeItem(key);
    return completed;
  }
  function sendChunk(id, offset, blob, file, position, total) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PATCH", `/api/uploads/${id}?offset=${offset}`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.timeout = 180000;
      xhr.upload.onprogress = (e) => {
        const percent = Math.min(
          100,
          Math.round(((offset + e.loaded) * 100) / file.size),
        );
        get("uploadBar").value = percent;
        get("uploadPercent").textContent = `${percent}%`;
        get("uploadLabel").textContent =
          `${position}/${total} · ${file.name}${offset ? " · resumable" : ""}`;
      };
      xhr.onload = () => {
        let result;
        try {
          result = JSON.parse(xhr.responseText);
        } catch {
          reject(Error("Unexpected upload response"));
          return;
        }
        xhr.status >= 200 && xhr.status < 300
          ? resolve(result)
          : reject(Error(result.detail || "Upload failed"));
      };
      xhr.onerror = () => reject(Error("Connection lost during upload"));
      xhr.ontimeout = () => reject(Error("Upload timed out"));
      xhr.send(blob);
    });
  }
  window.WorkspaceUI = {
    upload,
    hydrateSettings: hydrate,
    settingsValues,
    decorateFile,
    updateQueue,
    exportDialog,
    compareDialog,
    filterQuery: () =>
      `&archive=${state.archive}${state.libraryProject ? "&project=" + encodeURIComponent(state.libraryProject) : ""}${state.libraryTag ? "&tag=" + encodeURIComponent(state.libraryTag) : ""}`,
  };
  mountSettings();
  mountFiles();
  mountBenchmarks();
  workspace()
    .then(() => loadSettings())
    .catch((e) => feedback(e.message, true));
})();

// Full-review realignment remains one compact action; selected passages use the context menu.
(() => {
  const controls = document.querySelector(".editor-tools");
  const action = document.createElement("button");
  action.type = "button";
  action.id = "realignReview";
  action.textContent = "Realign transcript";
  action.title = "Generate new CTC word timing for the saved reviewed text";
  action.addEventListener("click", async () => {
    if (!corrections.jobId) return;
    action.disabled = true;
    try {
      if (!(await saveReview()))
        throw Error("Save or resolve your current draft before realigning.");
      const id = corrections.jobId,
        text = document.querySelector("#correctionText").value,
        revision = corrections.revision;
      const task = await api(`/api/jobs/${id}/correction/realign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, revision }),
      });
      document.querySelector("#reviewSyncStatus").textContent =
        "Alignment queued. It runs when the transcription worker is available.";
      const deadline = Date.now() + 60 * 60 * 1000;
      while (Date.now() < deadline && corrections.jobId === id) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const status = await api("/api/alignments/" + task.id);
        if (status.status === "done") {
          if (
            text === document.querySelector("#correctionText").value &&
            revision === corrections.revision
          ) {
            window.ReviewSync?.refresh();
            feedback("Reviewed text realigned against the recording.");
          } else
            feedback(
              "Alignment completed for an earlier saved version. Realign the newer edits to use them.",
            );
          return;
        }
        if (status.status === "failed")
          throw Error(status.error || "Alignment failed.");
      }
    } catch (error) {
      feedback(error.message, true);
    } finally {
      action.disabled = false;
    }
  });
  controls.append(action);
})();
