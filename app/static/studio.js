"use strict";
let routeVersion = 0;
const corrections = {
  jobId: null,
  filename: "",
  revision: 0,
  savedText: "",
  timer: null,
  pending: null,
  media: null,
  conflict: false,
  request: 0,
};
const $ = (selector) => document.querySelector(selector);
const ui = {
  chosen: [],
  jobs: [],
  filter: "all",
  uploading: false,
  lastJobs: "",
  deleting: null,
  transcript: "",
};
const active = (job) =>
  ["queued", "preparing", "processing"].includes(job.status);
const node = (tag, cls, value) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (value !== undefined) n.textContent = value;
  return n;
};
const duration = (seconds) => {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds))
    return "—";
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60),
    s = seconds % 60;
  return m < 60
    ? `${m}m ${String(s).padStart(2, "0")}s`
    : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
};
const speed = (value) =>
  Number.isFinite(value) && value > 0 ? `${value.toFixed(2)}×` : "—";
const factor = (value) =>
  Number.isFinite(value) && value > 0 ? value.toFixed(2) : "—";
const bytes = (value) =>
  value >= 1024 ** 3
    ? `${(value / 1024 ** 3).toFixed(1)} GB`
    : `${(value / 1024 ** 2).toFixed(1)} MB`;
const endpoint = (id, format) =>
  `/api/jobs/${encodeURIComponent(id)}/download/${format}`;
async function api(url, options = {}) {
  const r = await fetch(url, {
    ...options,
    signal: AbortSignal.timeout(15000),
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      detail = (await r.json()).detail || detail;
    } catch {}
    throw Error(detail);
  }
  return r.status === 204 ? null : r.json();
}
function feedback(text, error = false) {
  $("#message").textContent = text;
  $("#message").className = `feedback ${error ? "error" : "success"}`;
}
function selectFiles(files) {
  if (ui.uploading) return;
  ui.chosen = Array.from(files);
  renderSelected();
}
function renderSelected() {
  const holder = $("#selectedFiles");
  holder.replaceChildren();
  ui.chosen.forEach((file, i) => {
    const row = node("div", "selected-file");
    row.append(
      node("span", "", file.name),
      node("small", "", bytes(file.size)),
    );
    const remove = node("button", "", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${file.name} from selection`);
    remove.disabled = ui.uploading;
    remove.addEventListener("click", () => {
      ui.chosen.splice(i, 1);
      renderSelected();
    });
    row.append(remove);
    holder.append(row);
  });
  $("#submit").disabled = ui.uploading || !ui.chosen.length;
  $("#files").disabled = ui.uploading;
}
$("#files").addEventListener("change", (e) => selectFiles(e.target.files));
$("#drop").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    if (!ui.uploading) $("#files").click();
  }
});
for (const event of ["dragenter", "dragover"])
  $("#drop").addEventListener(event, (e) => {
    e.preventDefault();
    $("#drop").classList.add("drag");
  });
for (const event of ["dragleave", "drop"])
  $("#drop").addEventListener(event, (e) => {
    e.preventDefault();
    $("#drop").classList.remove("drag");
  });
$("#drop").addEventListener("drop", (e) => selectFiles(e.dataTransfer.files));
function upload(file, settings, position, total) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(),
      form = new FormData();
    form.append("file", file);
    for (const [key, value] of Object.entries(settings))
      form.append(key, value);
    xhr.open("POST", "/api/jobs");
    xhr.timeout = 30 * 60 * 1000;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const percent = Math.round((e.loaded / e.total) * 100);
        $("#uploadBar").value = percent;
        $("#uploadPercent").textContent = `${percent}%`;
        $("#uploadLabel").textContent =
          percent === 100
            ? "Checking audio…"
            : `${position}/${total} · ${file.name}`;
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300)
        resolve(JSON.parse(xhr.responseText));
      else {
        let detail = `HTTP ${xhr.status}`;
        try {
          detail = JSON.parse(xhr.responseText).detail || detail;
        } catch {}
        reject(Error(detail));
      }
    };
    xhr.onerror = () => reject(Error("Connection lost during upload"));
    xhr.ontimeout = () => reject(Error("Upload timed out"));
    xhr.send(form);
  });
}
$("#submit").addEventListener("click", async () => {
  if (ui.uploading || !ui.chosen.length) return;
  const files = [...ui.chosen],
    failed = [],
    settings = {
      language: $("#language").value,
      output_language: $("#outputLanguage").value,
      number_style: $("#numberStyle").value,
      segment_seconds: $("#duration").value,
      diarize: String($("#diarize").checked),
    };
  ui.uploading = true;
  renderSelected();
  $("#uploadProgress").hidden = false;
  let succeeded = 0,
    uploadedId = null;
  for (const [i, file] of files.entries()) {
    $("#uploadBar").value = 0;
    $("#uploadPercent").textContent = "0%";
    $("#uploadLabel").textContent = `${i + 1}/${files.length} · ${file.name}`;
    try {
      const created = await upload(file, settings, i + 1, files.length);
      uploadedId = created.id;
      succeeded++;
      await refresh();
    } catch (e) {
      failed.push({ file, error: e.message });
    }
  }
  ui.uploading = false;
  ui.chosen = failed.map((f) => f.file);
  $("#files").value = "";
  $("#uploadProgress").hidden = true;
  renderSelected();
  feedback(
    `${succeeded} recording${succeeded === 1 ? "" : "s"} queued.${failed.length ? " " + failed.map((f) => `${f.file.name}: ${f.error}`).join("; ") : " You can add more files while these process."}`,
    !!failed.length,
  );
  await refresh();
  if (uploadedId) {
    ui.fileStatus="all";ui.filePage=1;$("#fileSort").value="newest";renderJobs();
    switchTab("files");
    history.replaceState(
      {},
      "",
      `/files?job=${encodeURIComponent(uploadedId)}`,
    );
    const card = document.querySelector(`[data-job-id="${uploadedId}"]`);
    if (card) {
      card.classList.add("recent-upload");
      card.tabIndex = -1;
      card.focus({ preventScroll: true });
      card.scrollIntoView({
        block: "center",
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
      });
    }
  }
});
function stat(label, value, cls = "") {
  const item = node("div", `job-stat ${cls}`);
  item.append(node("span", "", label), node("b", "", value));
  return item;
}
function elapsed(job) {
  if (["processing", "preparing"].includes(job.status) && job.started_at)
    return Math.max(0, (Date.now() - Date.parse(job.started_at)) / 1000);
  return job.wall_s;
}
function renderJobs() {
  if (document.querySelector(".job-menu[open]")) return false;
  let holder = $("#jobs"),
    query = "",
    status = ui.fileStatus || "all",
    order = $("#fileSort").value;
  let jobs = ui.jobs.filter(
    (j) =>
      j.filename.toLocaleLowerCase().includes(query) &&
      (status === "all" ||
        (status === "processing"
          ? ["preparing", "processing"].includes(j.status)
          : j.status === status)),
  );
  const ranks = { queued: 0, preparing: 1, processing: 1, done: 2, failed: 3 };
  jobs.sort((a, b) =>
    order === "oldest"
      ? a.created_at.localeCompare(b.created_at)
      : order === "name"
        ? a.filename.localeCompare(b.filename)
        : order === "status"
          ? ranks[a.status] - ranks[b.status] ||
            b.created_at.localeCompare(a.created_at)
          : b.created_at.localeCompare(a.created_at),
  );
  $("#queueCount").textContent = ui.jobs.length;
  for (const button of document.querySelectorAll("[data-file-status]")) {
    const selected = button.dataset.fileStatus === status;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
    const count = ui.jobs.filter(
      (j) =>
        button.dataset.fileStatus === "all" ||
        (button.dataset.fileStatus === "processing"
          ? ["preparing", "processing"].includes(j.status)
          : j.status === button.dataset.fileStatus),
    ).length;
    button.querySelector("span").textContent = count;
  }
  const allCount = jobs.length;
  const pageSize = ui.filePageSize || 10;
  ui.filePage = Math.max(
    1,
    Math.min(ui.filePage || 1, Math.ceil(allCount / pageSize) || 1),
  );
  paginateControl(
    "#filePagination",
    allCount,
    ui.filePage,
    pageSize,
    (page, size) => {
      ui.filePage = page;
      ui.filePageSize = size;
      renderJobs();
    },
  );
  jobs = jobs.slice((ui.filePage - 1) * pageSize, ui.filePage * pageSize);
  holder.replaceChildren();
  if (!jobs.length) {
    const empty = node("div", "empty-state");
    empty.append(
      node("h3", "", ui.jobs.length ? "No matching files" : "No files yet"),
      node(
        "p",
        "",
        ui.jobs.length
          ? "Try another status or search."
          : "Add a recording in Upload to get started.",
      ),
    );
    holder.append(empty);
    return true;
  }
  for (const job of jobs) {
    const card = node("article", `job ${job.status}`);
    card.dataset.jobId = job.id;
    const head = node("div", "job-head"),
      icon = node(
        "div",
        "file-icon",
        job.filename.split(".").pop().toUpperCase().slice(0, 5),
      ),
      main = node("div", "job-name-wrap");
    main.append(node("h3", "job-name", job.filename));
    const hint =
      job.output_language && job.output_language !== "source"
        ? `${job.language.toUpperCase()} → ${job.output_language.toUpperCase()}`
        : job.language === "auto"
          ? "Auto language"
          : job.language.toUpperCase();
    const metadata = [`${duration(job.duration_s)} audio`, hint];
    if (job.status === "done")
      metadata.push(`${duration(job.wall_s)} processing`, speed(job.rtfx));
    else if (["processing", "preparing"].includes(job.status))
      metadata.push("Processing now");
    main.append(node("p", "job-subtitle", metadata.join(" · ")));
    head.append(
      icon,
      main,
      node(
        "span",
        `status ${job.status}`,
        {
          queued: "Queued",
          preparing: "Preparing",
          processing: "Transcribing",
          done: "Completed",
          failed: "Failed",
        }[job.status],
      ),
    );
    if (job.status === "done") {
      const open = node("button", "secondary preview-button", "Open player");
      open.addEventListener("click", () => showPlayer(job));
      head.append(open);
    }
    const menu = node("details", "job-menu"),
      summary = node("summary", "", "⋯");
    summary.setAttribute("aria-label", `Actions for ${job.filename}`);
    menu.append(summary);
    const items = node("div", "job-menu-items");
    function action(label, callback, danger = false) {
      const button = node("button", danger ? "menu-danger" : "", label);
      button.type = "button";
      button.addEventListener("click", () => {
        menu.open = false;
        callback();
      });
      items.append(button);
    }
    if (job.status === "done") {
      action("Correct transcript", () => showCorrections(job.id));
      for (const format of job.srt_available
        ? ["txt", "json", "srt"]
        : ["txt", "json"]) {
        const link = node("a", "", `Download ${format.toUpperCase()}`);
        link.href = endpoint(job.id, format);
        link.addEventListener("click", () => {
          menu.open = false;
        });
        items.append(link);
      }
    }
    if (["done", "failed"].includes(job.status))
      action("Retry", async () => {
        try {
          await api(`/api/jobs/${encodeURIComponent(job.id)}/retry`, {
            method: "POST",
          });
          feedback("Recording queued for another run.");
          await refresh();
        } catch (error) {
          feedback(error.message, true);
        }
      });
    if (!["processing", "preparing"].includes(job.status))
      action(
        "Delete",
        () => {
          ui.deleting = job;
          $("#deleteFilename").textContent = job.filename;
          $("#deleteDialog").showModal();
        },
        true,
      );
    else items.append(node("span", "menu-hint", "Processing is active"));
    menu.append(items);
    menu.addEventListener("toggle", () => {
      if (!menu.open && menu.isConnected) renderJobs();
    });
    head.append(menu);
    card.append(head);
    if (active(job)) {
      const progress = node("div", "job-progress"),
        bar = node("progress");
      bar.max = 100;
      bar.value = job.progress;
      bar.setAttribute("aria-label", `${job.filename} transcription progress`);
      const label = node("div", "job-progress-label");
      label.append(
        node(
          "span",
          "",
          job.status === "queued"
            ? "Waiting for an available slot"
            : job.status === "preparing"
              ? "Preparing the recording"
              : `${job.completed_segments} of ${job.total_segments} segments`,
        ),
        node("strong", "", `${job.progress}%`),
      );
      progress.append(bar, label);
      card.append(progress);
    }
    if (job.error) card.append(node("p", "job-error", job.error));
    if (job.status !== "queued") {
      const details = node("details", "job-details"),
        title = node("summary", "", "Details"),
        stats = node("div", "job-stats");
      const timeStat = stat(
        active(job) ? "Elapsed" : "Processing time",
        duration(elapsed(job)),
      );
      if (active(job)) timeStat.querySelector("b").dataset.elapsedId = job.id;
      stats.append(
        timeStat,
        stat(
          active(job) ? "Speed · provisional" : "Speed · RTFx",
          speed(job.rtfx),
          "speed",
        ),
        stat("Real-time factor", job.rtfx ? factor(1 / job.rtfx) : "—"),
        stat("Inference time", duration(job.inference_s)),
      );
      details.append(title, stats);
      if (job.status === "done" && !job.srt_available)
        details.append(
          node(
            "p",
            "srt-note",
            "Full subtitle alignment is unavailable. Any matching timed words remain accessible in Player.",
          ),
        );
      details.open = !!ui.expandedFiles?.has(job.id);
      details.addEventListener("toggle", () => {
        ui.expandedFiles ??= new Set();
        details.open
          ? ui.expandedFiles.add(job.id)
          : ui.expandedFiles.delete(job.id);
      });
      card.append(details);
    }
    holder.append(card);
  }
  return true;
}

function renderHistory(history) {
  const container = $("#history");
  container.replaceChildren();
  const today = new Date(),
    days = Array.from({ length: 7 }, (_, i) => {
      const date = new Date(
        Date.UTC(
          today.getUTCFullYear(),
          today.getUTCMonth(),
          today.getUTCDate() - 6 + i,
        ),
      );
      const day = date.toISOString().slice(0, 10);
      return {
        date,
        day,
        seconds: history.find((x) => x.day === day)?.audio_s || 0,
      };
    });
  const max = Math.max(1, ...days.map((d) => d.seconds));
  for (const d of days) {
    const col = node("div", "history-day"),
      bar = node("div", "history-bar");
    bar.style.height = `${Math.max(2, (d.seconds / max) * 64)}px`;
    col.title = `${d.day}: ${duration(d.seconds)} completed`;
    bar.setAttribute("aria-label", col.title);
    col.append(
      bar,
      node(
        "span",
        "",
        d.date
          .toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" })
          .slice(0, 2),
      ),
    );
    container.append(col);
  }
}
function renderStats(stats) {
  $("#combinedThroughput").textContent = stats.model_throughput_rtfx
    ? `Combined model throughput: ${speed(stats.model_throughput_rtfx)} · ${stats.model_traced_files} completed files with full request tracing. Overlapping requests count once.`
    : "Combined model throughput appears after a traced file completes.";
  $("#audioTotal").textContent = duration(stats.audio_s);
  $("#wallTotal").textContent = duration(stats.wall_s);
  $("#speedTotal").textContent = speed(stats.rtfx);
  $("#rtfTotal").textContent = factor(stats.rtf);
  const count = stats.counts.done || 0;
  $("#fileTotal").textContent =
    `${count} completed file${count === 1 ? "" : "s"} · ${stats.counts.failed || 0} failed`;
  $("#speedHint").textContent = stats.rtfx
    ? `${(stats.rtfx * 60).toFixed(1)} audio min / processing hour`
    : "Measured after your first transcription";
  renderHistory(stats.history);
  const running = ui.jobs.find((j) =>
      ["preparing", "processing"].includes(j.status),
    ),
    pending = ui.jobs.filter((j) => j.status === "queued").length;
  $("#queueSummary").hidden = !running && !pending;
  $("#activeSummary").textContent = running
    ? `Working on ${running.filename}`
    : "Queue is waiting for the model";
  $("#pendingSummary").textContent = `${pending} waiting`;
}
async function showPreview(job) {
  $("#previewTitle").textContent = job.filename;
  $("#previewText").textContent = "Loading transcript…";
  $("#copyStatus").textContent = "";
  $("#copyTranscript").disabled = true;
  $("#previewMeta").textContent =
    `${duration(job.duration_s)} audio · ${speed(job.rtfx)} throughput · ${job.rtfx ? factor(1 / job.rtfx) : "—"} RTF`;
  $("#previewDownload").href = endpoint(job.id, "txt");
  $("#preview").showModal();
  try {
    const result = await api(endpoint(job.id, "json"));
    ui.transcript = result.text || "";
    $("#previewText").textContent =
      ui.transcript || "No speech detected in this recording.";
    $("#copyTranscript").disabled = !ui.transcript;
  } catch (e) {
    $("#previewText").textContent = `Could not load transcript: ${e.message}`;
  }
}
$("#closePreview").addEventListener("click", () => $("#preview").close());
$("#copyTranscript").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(ui.transcript);
    $("#copyStatus").textContent = "Copied to clipboard";
  } catch {
    $("#copyStatus").textContent =
      "Clipboard unavailable. Select and copy the text.";
  }
});
for (const id of ["closeDelete", "cancelDelete"])
  $(`#${id}`).addEventListener("click", () => $("#deleteDialog").close());
$("#confirmDelete").addEventListener("click", async () => {
  if (!ui.deleting) return;
  $("#confirmDelete").disabled = true;
  try {
    await api(`/api/jobs/${encodeURIComponent(ui.deleting.id)}`, {
      method: "DELETE",
    });
    $("#deleteDialog").close();
    feedback("Recording deleted.");
    await refresh();
  } catch (e) {
    feedback(e.message, true);
    $("#deleteDialog").close();
  } finally {
    $("#confirmDelete").disabled = false;
    ui.deleting = null;
  }
});
for (const button of document.querySelectorAll(".filter"))
  button.addEventListener("click", () => {
    ui.filter = button.dataset.filter;
    for (const b of document.querySelectorAll(".filter")) {
      b.classList.toggle("active", b === button);
      b.setAttribute("aria-pressed", String(b === button));
    }
    renderJobs();
  });

let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const [jobs, stats] = await Promise.all([
      api("/api/jobs?all=true"),
      api("/api/stats"),
    ]);
    ui.jobs = jobs.jobs;
    const snapshot = JSON.stringify(ui.jobs);
    if (snapshot !== ui.lastJobs) {
      if (renderJobs() !== false) ui.lastJobs = snapshot;
    }
    renderStats(stats);
    renderFileStatistics();
    $("#refreshStatus").textContent = "● LIVE UPDATES";
  } catch {
    $("#refreshStatus").textContent = "RECONNECTING…";
  } finally {
    refreshing = false;
  }
}
async function refreshEngine() {
  try {
    const info = await api("/api/system");
    renderInfo(info);
    $("#engine").classList.toggle("ready", info.engine_ready);
    $("#engineLabel").textContent = info.engine_ready
      ? "Model ready"
      : "Model loading / unavailable";
    $("#backendLabel").textContent =
      info.backend === "hybrid" ? "GPU + RAM · HYBRID" : "CPU · RAM";
    $("#uploadLimit").textContent =
      `Up to ${info.max_upload_mb >= 1024 ? `${(info.max_upload_mb / 1024).toFixed(0)} GB` : `${info.max_upload_mb} MB`} per file`;
    $("#storage").textContent =
      `${info.disk_free_gib.toFixed(1)} GiB free on disk`;
    $("#retention").textContent = info.retention_days
      ? `Completed files retained for ${info.retention_days} days.`
      : "Files stay here until you delete them.";
  } catch {
    $("#engineLabel").textContent = "Studio disconnected";
    $("#engine").classList.remove("ready");
  }
}
setInterval(() => {
  for (const n of document.querySelectorAll("[data-elapsed-id]")) {
    const job = ui.jobs.find((j) => j.id === n.dataset.elapsedId);
    if (job) n.textContent = duration(elapsed(job));
  }
}, 1000);
refresh();
refreshEngine();
setInterval(refresh, 2500);
setInterval(refreshEngine, 8000);

// Tabs keep the file queue, synchronized player and performance readout separate.
function switchTab(name, navigate = true) {
  routeVersion++;
  const selectedJob =
    name === "player"
      ? playback.jobId
      : name === "corrections"
        ? corrections.jobId
        : null;
  const target =
    `/${name === "stats" ? "statistics" : name}` +
    (selectedJob ? `?job=${encodeURIComponent(selectedJob)}` : "");
  if (navigate && location.pathname + location.search !== target)
    history.pushState({}, "", target);
  document.title = `${name.charAt(0).toUpperCase() + name.slice(1)} · Nemotron Transcribe`;
  for (const button of document.querySelectorAll("[data-tab]")) {
    const selected = button.dataset.tab === name;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  }
  for (const panel of document.querySelectorAll(".tab-panel"))
    panel.hidden = panel.id !== `${name}Tab`;
}
for (const button of document.querySelectorAll("[data-tab]")) {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
  button.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const tabs = [...document.querySelectorAll("[data-tab]")],
      index = tabs.indexOf(button),
      next =
        e.key === "Home"
          ? 0
          : e.key === "End"
            ? tabs.length - 1
            : (index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) %
              tabs.length;
    tabs[next].focus();
    switchTab(tabs[next].dataset.tab);
  });
}
$("#playerBack").addEventListener("click", () => {
  switchTab("files");
  $("#filesNav").focus();
});
function renderFileStatistics() {
  renderCorrectionPicker();
  const body = $("#fileStatistics");
  body.replaceChildren();
  const completed = ui.jobs.filter((j) => j.status === "done"),
    size = ui.statsPageSize || 10;
  ui.statsPage = Math.max(
    1,
    Math.min(ui.statsPage || 1, Math.ceil(completed.length / size) || 1),
  );
  paginateControl(
    "#statsPagination",
    completed.length,
    ui.statsPage,
    size,
    (page, pageSize) => {
      ui.statsPage = page;
      ui.statsPageSize = pageSize;
      renderFileStatistics();
    },
  );
  for (const job of completed.slice(
    (ui.statsPage - 1) * size,
    ui.statsPage * size,
  )) {
    const row = node("tr");
    for (const value of [
      job.filename,
      duration(job.duration_s),
      duration(job.wall_s),
      speed(job.rtfx),
      job.rtfx ? factor(1 / job.rtfx) : "—",
      duration(job.inference_s),
    ])
      row.append(node("td", "", value));
    body.append(row);
  }
  if (!body.childNodes.length) {
    const cell = node("td", "", "Statistics appear when a recording finishes.");
    cell.colSpan = 6;
    const row = node("tr");
    row.append(cell);
    body.append(row);
  }
}
const playback = {
  media: null,
  words: [],
  maxWordDuration: 0,
  highlighted: new Set(),
  frame: null,
  request: 0,
  manualScrollUntil: 0,
};
const clock = (seconds) => {
  if (!Number.isFinite(seconds)) return "0:00";
  const s = Math.max(0, Math.floor(seconds)),
    m = Math.floor(s / 60);
  return m >= 60
    ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
    : `${m}:${String(s % 60).padStart(2, "0")}`;
};
function canonical(text) {
  return text
    .normalize("NFC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]/gu, "");
}
function alignedRanges(segment) {
  if (
    segment.alignment_valid !== true ||
    !Array.isArray(segment.words) ||
    !segment.words.length
  )
    return null;
  const text = segment.text.normalize("NFC"),
    indices = [];
  let normalized = "",
    utf16Index = 0;
  for (const char of text) {
    const value = canonical(char);
    for (const letter of value) {
      normalized += letter;
      indices.push({ start: utf16Index, end: utf16Index + char.length });
    }
    utf16Index += char.length;
  }
  if (normalized !== segment.words.map((w) => canonical(w.word)).join(""))
    return null;
  let cursor = 0;
  const ranges = [];
  for (const word of segment.words) {
    const length = canonical(word.word).length,
      start = word.start,
      end = word.end;
    if (
      !length ||
      !Number.isFinite(start) ||
      !Number.isFinite(end) ||
      start < 0 ||
      end <= start ||
      (segment.duration_s && end > segment.duration_s + 0.1) ||
      !Number.isFinite(segment.start_seconds)
    )
      return null;
    ranges.push({
      start: indices[cursor].start,
      end: indices[cursor + length - 1].end,
      from: segment.start_seconds + start,
      to: segment.start_seconds + end,
      speaker: word.speaker,
      segmentIndex: segment.index,
    });
    cursor += length;
  }
  return { text, ranges };
}
function wordButton(word, text) {
  const button = node("button", "timed-word", text);
  button.type = "button";
  button.dataset.start = word.start;
  button.dataset.end = word.end;
  button.title = `Play from ${clock(word.start)}`;
  if (word.speaker !== undefined && word.speaker !== null) {
    button.dataset.speakerColor = String(
      ((Number(String(word.speaker).replace(/[^0-9]/g, "")) || 0) % 3) + 1,
    );
    button.title += ` · Segment ${word.segmentIndex + 1}, speaker ${word.speaker}`;
  }
  button.addEventListener("click", () => {
    if (!playback.media) return;
    playback.media.currentTime = Math.max(
      0,
      word.start - Number($("#syncOffset").value || 0),
    );
    playback.manualScrollUntil = 0;
    updateWordHighlight();
    playback.media.play().catch(() => {
      $("#mediaError").textContent = "Press Play to begin playback.";
    });
  });
  playback.words.push({ ...word, element: button });
  playback.maxWordDuration = Math.max(
    playback.maxWordDuration,
    word.end - word.start,
  );
  return button;
}
function renderSyncedTranscript(result, preserveFilter = false) {
  playback.result = result;
  const container = $("#syncedTranscript");
  container.replaceChildren();
  playback.words = [];
  playback.highlighted = new Set();
  playback.maxWordDuration = 0;
  const groups = new Map();
  for (const segment of result.segments || [])
    for (const word of segment.words || []) {
      if (
        word.speaker === undefined ||
        word.speaker === null ||
        !Number.isFinite(word.start) ||
        !Number.isFinite(word.end) ||
        word.start < 0 ||
        word.end <= word.start
      )
        continue;
      const key = `${segment.index}|${word.speaker}`;
      if (!groups.has(key))
        groups.set(key, {
          index: segment.index,
          speaker: word.speaker,
          offset: segment.start_seconds,
          words: [],
        });
      groups.get(key).words.push(word);
    }
  const selected = $("#speakerFilter").value;
  $("#speakerFilter").replaceChildren(node("option", "", "All speakers"));
  $("#speakerFilter").firstChild.value = "all";
  for (const [key, group] of groups) {
    const option = node(
      "option",
      "",
      `Segment ${group.index + 1} · Speaker ${group.speaker}`,
    );
    option.value = key;
    $("#speakerFilter").append(option);
  }
  if (
    preserveFilter &&
    [...$("#speakerFilter").options].some((o) => o.value === selected)
  )
    $("#speakerFilter").value = selected;
  $("#transcriptView").querySelector("[value=speakers]").disabled =
    !groups.size;
  if (!groups.size) $("#transcriptView").value = "full";
  const bySpeaker = $("#transcriptView").value === "speakers";
  $("#speakerFilter").hidden = !bySpeaker;
  let partial = false,
    aligned = 0,
    unaligned = 0;
  if (bySpeaker) {
    for (const [key, group] of groups) {
      if (
        $("#speakerFilter").value !== "all" &&
        $("#speakerFilter").value !== key
      )
        continue;
      const paragraph = node("p", "transcript-segment");
      paragraph.append(
        node(
          "span",
          "speaker-label",
          `Segment ${group.index + 1} · Speaker ${group.speaker}`,
        ),
      );
      for (const word of group.words) {
        paragraph.append(
          wordButton(
            {
              start: group.offset + word.start,
              end: group.offset + word.end,
              speaker: word.speaker,
              segmentIndex: group.index,
            },
            word.word,
          ),
          document.createTextNode(" "),
        );
      }
      container.append(paragraph);
    }
    $("#alignmentNote").textContent =
      "Word-aligned text grouped by speaker. IDs are local to each segment; alignment wording may differ from the formatted transcript.";
  } else {
    for (const segment of result.segments || []) {
      if (!segment.text?.trim()) continue;
      const paragraph = node("p", "transcript-segment");
      let position = 0;
      const mapped = segment.playback_words;
      let ranges;
      if (Array.isArray(mapped))
        ranges = mapped.map((w) => ({
          start: w.char_start,
          end: w.char_end,
          from: segment.start_seconds + w.start,
          to: segment.start_seconds + w.end,
          speaker: w.speaker,
          segmentIndex: segment.index,
        }));
      else ranges = alignedRanges(segment)?.ranges;
      if (ranges?.length) {
        for (const range of ranges) {
          if (
            range.start < position ||
            range.end > segment.text.length ||
            !Number.isFinite(range.from) ||
            !Number.isFinite(range.to) ||
            range.to <= range.from
          )
            continue;
          paragraph.append(
            document.createTextNode(segment.text.slice(position, range.start)),
          );
          paragraph.append(
            wordButton(
              {
                start: range.from,
                end: range.to,
                speaker: range.speaker,
                segmentIndex: segment.index,
              },
              segment.text.slice(range.start, range.end),
            ),
          );
          position = range.end;
        }
        paragraph.append(document.createTextNode(segment.text.slice(position)));
        aligned++;
        if (!segment.alignment_valid) partial = true;
      } else {
        paragraph.append(document.createTextNode(segment.text));
        paragraph.append(
          node(
            "span",
            "unaligned-label",
            "Word timing unavailable for this segment.",
          ),
        );
        unaligned++;
      }
      container.append(paragraph);
    }
    $("#alignmentNote").textContent =
      aligned && !unaligned && !partial
        ? "Click any timed word to seek. Highlighting follows playback."
        : aligned
          ? "Only words matched to real model alignment are highlighted. Unmatched text remains readable without invented timings."
          : "No valid word timing is available. The full transcript remains readable.";
  }
  if (!container.childNodes.length)
    container.append(node("p", "", result.text || "No speech detected."));
  playback.words.sort((a, b) => a.start - b.start);
  $("#jumpFirstWord").disabled = !playback.words.length;
  $("#speakerLegend").replaceChildren();
  if (groups.size)
    $("#speakerLegend").append(
      node(
        "span",
        "speaker-key",
        `${groups.size} segment/speaker groups · use By speaker to filter`,
      ),
    );
  container.scrollTop = 0;
  updateWordHighlight();
}

function updateWordHighlight() {
  const media = playback.media;
  if (!media) return;
  const time = media.currentTime + Number($("#syncOffset").value || 0);
  $("#playbackTime").textContent =
    `${clock(media.currentTime)} / ${clock(media.duration)}`;
  const words = playback.words;
  let lo = 0,
    hi = words.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (words[mid].start <= time) lo = mid + 1;
    else hi = mid;
  }
  const current = new Set();
  for (
    let i = lo - 1;
    i >= 0 && words[i].start >= time - playback.maxWordDuration;
    i--
  )
    if (words[i].end > time) current.add(words[i].element);
  for (const element of playback.highlighted)
    if (!current.has(element)) {
      element.classList.remove("speaking");
      element.removeAttribute("aria-current");
    }
  let newest = null;
  for (const element of current)
    if (!playback.highlighted.has(element)) {
      element.classList.add("speaking");
      element.setAttribute("aria-current", "true");
      newest = element;
    }
  playback.highlighted = current;
  const first = playback.words[0];
  $("#syncStatus").textContent = current.size
    ? "Following aligned words"
    : first && time < first.start
      ? `First aligned speech at ${clock(first.start)}. No aligned words before this point.`
      : "No aligned word at the current playback position.";
  if (
    newest &&
    $("#followPlayback").checked &&
    performance.now() >= playback.manualScrollUntil &&
    !$("#playerTab").hidden
  ) {
    const container = $("#syncedTranscript"),
      bounds = container.getBoundingClientRect(),
      word = newest.getBoundingClientRect();
    if (word.top < bounds.top + 25 || word.bottom > bounds.bottom - 45)
      container.scrollBy({
        top: word.top - bounds.top - bounds.height * 0.4,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
      });
  }
}
function animatePlayback() {
  updateWordHighlight();
  if (playback.media && !playback.media.paused && !playback.media.ended)
    playback.frame = requestAnimationFrame(animatePlayback);
}
async function showPlayer(job, navigate = true) {
  playback.jobId = job.id;
  $("#transcriptView").value = "full";
  const request = ++playback.request;
  if (playback.media) playback.media.pause();
  if (playback.frame) cancelAnimationFrame(playback.frame);
  switchTab("player", navigate);
  $("#playerTitle").textContent = job.filename;
  $("#playerSubtitle").textContent =
    `${duration(job.duration_s)} · ${job.language.toUpperCase()} · ${speed(job.rtfx)} processing speed`;
  $("#playerEmpty").hidden = true;
  $("#playerWorkspace").hidden = false;
  $("#mediaError").textContent = "";
  $("#alignmentNote").textContent = "Loading transcript…";
  $("#syncedTranscript").replaceChildren(node("p", "", "Loading…"));
  $("#syncOffset").value = "0";
  $("#mediaMount").replaceChildren();
  playback.words = [];
  playback.highlighted = new Set();
  playback.media = null;
  $("#playerTxt").href = endpoint(job.id, "txt");
  $("#playerJson").href = endpoint(job.id, "json");
  try {
    const result = await api(endpoint(job.id, "json"));
    if (request !== playback.request) return;
    renderSyncedTranscript(result);
    const video = ["mp4", "webm", "mkv", "mov", "avi"].includes(
        job.filename.split(".").pop().toLowerCase(),
      ),
      media = node(video ? "video" : "audio");
    media.controls = true;
    media.preload = "metadata";
    media.src = `/api/jobs/${encodeURIComponent(job.id)}/media`;
    media.setAttribute("aria-label", job.filename);
    media.playsInline = true;
    if (!video) {
      const cover = node("div", "audio-cover");
      cover.setAttribute("aria-hidden", "true");
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 64 64");
      svg.setAttribute("fill", "none");
      svg.setAttribute("stroke", "currentColor");
      for (const [x, y] of [
        [10, 25],
        [18, 16],
        [26, 8],
        [34, 20],
        [42, 12],
        [50, 23],
      ]) {
        const line = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "path",
        );
        line.setAttribute("d", `M${x} ${y}V${64 - y}`);
        line.setAttribute("stroke-linecap", "round");
        svg.append(line);
      }
      cover.append(svg);
      $("#mediaMount").append(cover);
    }
    $("#mediaMount").append(media);
    playback.media = media;
    media.playbackRate = Number($("#playbackSpeed").value);
    for (const event of [
      "timeupdate",
      "seeking",
      "seeked",
      "loadedmetadata",
      "durationchange",
    ])
      media.addEventListener(event, updateWordHighlight);
    media.addEventListener("play", () => {
      if (playback.frame) cancelAnimationFrame(playback.frame);
      animatePlayback();
    });
    media.addEventListener("pause", () => {
      if (playback.frame) cancelAnimationFrame(playback.frame);
      updateWordHighlight();
    });
    media.addEventListener("ended", () => {
      if (playback.frame) cancelAnimationFrame(playback.frame);
      updateWordHighlight();
    });
    media.addEventListener("error", () => {
      $("#mediaError").textContent =
        "This browser could not play the media format. Download the original recording and try a compatible player.";
    });
  } catch (e) {
    if (request === playback.request)
      $("#alignmentNote").textContent =
        `Could not load transcript: ${e.message}`;
  }
}
$("#playbackSpeed").addEventListener("change", () => {
  if (playback.media)
    playback.media.playbackRate = Number($("#playbackSpeed").value);
});
for (const event of ["wheel", "touchstart"])
  $("#syncedTranscript").addEventListener(
    event,
    () => {
      playback.manualScrollUntil = performance.now() + 4000;
    },
    { passive: true },
  );

function infoRows(target, entries) {
  const list = $(target);
  list.replaceChildren();
  for (const [label, value] of entries) {
    const pair = node("div", "info-pair");
    pair.append(node("dt", "", label), node("dd", "", value ?? "Unavailable"));
    list.append(pair);
  }
}
function renderInfo(info) {
  const t = info.telemetry,
    lock = info.runtime,
    gpu = t?.gpu,
    ram = t?.ram;
  const number = (value, suffix, digits = 1) =>
    Number.isFinite(value)
      ? `${value.toFixed(digits)} ${suffix}`
      : "Unavailable";
  $("#telemetryStatus").textContent = t
    ? `Updated ${new Date(t.sampled_at).toLocaleTimeString()} · live readings refresh automatically`
    : "Live resource readings are unavailable. The monitor may be starting.";
  infoRows("#engineInfo", [
    ["Model", "Nemotron 3.5 Transcribe 30B-A4B"],
    ["Quantization", "IQ4_NL decoder + F16 speech encoder"],
    [
      "Mode",
      info.backend === "hybrid" ? "CUDA + CPU expert offload" : "CPU / RAM",
    ],
    ["Status", info.engine_ready ? "Ready" : "Loading / unavailable"],
    [
      "CPU expert placement",
      t?.cpu_moe_blocks
        ? `Routed experts in the first ${t.cpu_moe_blocks} decoder blocks are in RAM`
        : info.backend === "cpu"
          ? "Entire model in system RAM"
          : "Unavailable",
    ],
    [
      "Execution",
      info.backend === "hybrid"
        ? "Memory-mapped weights; one model, one worker"
        : "One model, one worker",
    ],
    ["Parallel file limit", String(info.settings?.parallel_jobs ?? 1)],
    ["Decode capacity", String(info.engine_capacity ?? 16)],
    ["Subtitles", "Only when model word alignment is valid"],
  ]);
  infoRows("#gpuInfo", [
    ["Device", gpu?.name],
    ["Total VRAM", number(gpu?.total_mib, "MiB", 0)],
    ["Used VRAM · all apps", number(gpu?.used_mib, "MiB", 0)],
    ["Model VRAM", gpu ? number(gpu.engine_used_mib, "MiB", 0) : null],
    ["GPU utilization", number(gpu?.utilization_percent, "%", 0)],
    ["Temperature", number(gpu?.temperature_c, "°C", 0)],
    ["Power", number(gpu?.power_w, "W")],
  ]);
  infoRows("#hostInfo", [
    ["Processor", t?.cpu?.name],
    ["Logical cores", t?.cpu?.logical_cores],
    ["Host CPU utilization", number(t?.cpu?.utilization_percent, "%", 0)],
    ["Model CPU consumption", number(t?.cpu?.engine_cpu_cores, "cores")],
    ["System RAM", number(ram?.total_gib, "GiB")],
    ["Available RAM", number(ram?.available_gib, "GiB")],
    ["Used RAM", number(ram?.used_gib, "GiB")],
    ["Model process RSS", number(t?.engine_rss_mib, "MiB", 0)],
    ["Swap used", number(ram?.swap_used_gib, "GiB")],
  ]);
  infoRows("#deploymentInfo", [
    ["Access", "Localhost · 127.0.0.1"],
    ["Orchestration", "Docker Compose"],
    ["Storage free", number(info.disk_free_gib, "GiB")],
    ["Upstream commit", lock?.upstream_commit],
    [
      "Hybrid patch",
      t?.patch_commit ?? (info.backend === "cpu" ? "Not active" : null),
    ],
    ["Model snapshot", lock?.model_revision],
    ["Upload limit", `${info.max_upload_mb} MB per file`],
    ["Accepted file types", (info.accepted_filetypes || []).join(", ")],
    [
      "Retention",
      info.retention_days
        ? `${info.retention_days} days for completed/failed jobs`
        : "Until manually deleted",
    ],
  ]);
}

$("#syncOffset").addEventListener("input", updateWordHighlight);
$("#jumpFirstWord").addEventListener("click", () => {
  const first = playback.words[0];
  if (first && playback.media) {
    playback.media.currentTime = Math.max(
      0,
      first.start - Number($("#syncOffset").value || 0),
    );
    updateWordHighlight();
  }
});
function languageOptions(select, languages, special, value) {
  select.replaceChildren(node("option", "", special[1]));
  select.firstChild.value = special[0];
  for (const [code, name] of Object.entries(languages).sort((a, b) =>
    a[1].localeCompare(b[1]),
  )) {
    const option = node("option", "", name);
    option.value = code;
    select.append(option);
  }
  select.value = value;
}
function speakerAvailability() {
  for (const [input, output, checkbox] of [
    ["#language", "#outputLanguage", "#diarize"],
    ["#defaultInputLanguage", "#defaultOutputLanguage", "#defaultDiarize"],
  ]) {
    const translated =
      $(output).value !== "source" && $(output).value !== $(input).value;
    $(checkbox).disabled = translated;
    if (translated) $(checkbox).checked = false;
  }
}
async function loadSettings() {
  try {
    const response = await api("/api/settings"),
      s = response.settings;
    for (const [id, special, value] of [
      ["#language", ["auto", "Auto-detect"], s.input_language],
      ["#outputLanguage", ["source", "Same as spoken"], s.output_language],
      ["#defaultInputLanguage", ["auto", "Auto-detect"], s.input_language],
      [
        "#defaultOutputLanguage",
        ["source", "Same as spoken"],
        s.output_language,
      ],
    ])
      languageOptions($(id), response.languages, special, value);
    $("#duration").value = s.segment_seconds;
    $("#numberStyle").value = s.number_style || "digits";
    $("#defaultNumberStyle").value = s.number_style || "digits";
    $("#diarize").checked = s.diarize;
    $("#defaultChunk").value = s.segment_seconds;
    $("#defaultDiarize").checked = s.diarize;
    $("#parallelLevel").value = [1, 2, 4, 8, 16].includes(s.parallel_jobs)
      ? String(s.parallel_jobs)
      : "custom";
    $("#customParallel").value = s.parallel_jobs;
    $("#customParallel").max = response.engine_capacity;
    $("#customParallelRow").hidden = $("#parallelLevel").value !== "custom";
    $("#parallelCapacity").textContent =
      `Runtime capacity: ${response.engine_capacity} shared decode slots. ${response.active_jobs} file(s) active. Reducing concurrency takes effect as active files finish.`;
    speakerAvailability();
  } catch (e) {
    $("#settingsMessage").textContent = `Could not load settings: ${e.message}`;
  }
}
$("#parallelLevel").addEventListener("change", () => {
  $("#customParallelRow").hidden = $("#parallelLevel").value !== "custom";
});
for (const id of [
  "#language",
  "#outputLanguage",
  "#defaultInputLanguage",
  "#defaultOutputLanguage",
])
  $(id).addEventListener("change", speakerAvailability);
$("#settingsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  const values = {
    parallel_jobs: Number(
      $("#parallelLevel").value === "custom"
        ? $("#customParallel").value
        : $("#parallelLevel").value,
    ),
    segment_seconds: Number($("#defaultChunk").value),
    input_language: $("#defaultInputLanguage").value,
    number_style: $("#defaultNumberStyle").value,
    output_language: $("#defaultOutputLanguage").value,
    diarize: $("#defaultDiarize").checked,
  };
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    await loadSettings();
    $("#settingsMessage").textContent =
      "Saved. Defaults apply to new uploads; queue concurrency updates automatically.";
  } catch (error) {
    $("#settingsMessage").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
loadSettings();

$("#transcriptView").addEventListener("change", () => {
  if (playback.result) renderSyncedTranscript(playback.result);
});
$("#speakerFilter").addEventListener("change", () => {
  if (playback.result) renderSyncedTranscript(playback.result, true);
});
async function restoreRoute() {
  const name =
      location.pathname.slice(1) === "statistics"
        ? "stats"
        : location.pathname.slice(1),
    tab = [
      "upload",
      "files",
      "player",
      "stats",
      "info",
      "settings",
      "corrections",
    ].includes(name)
      ? name
      : "upload";
  switchTab(tab, false);
  const version = routeVersion;
  const id = new URLSearchParams(location.search).get("job");
  if (tab === "player" && id && playback.jobId !== id) {
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (version !== routeVersion) return;
      if (job.status === "done") await showPlayer(job, false);
      else
        $("#playerSubtitle").textContent =
          "This recording is still processing. Open Files to follow its progress.";
    } catch (error) {
      $("#playerSubtitle").textContent = error.message;
    }
  } else if (tab === "corrections" && id && corrections.jobId !== id) {
    await showCorrections(id, false);
  } else if (tab === "files" && id) {
    for (let attempt = 0; attempt < 20; attempt++) {
      const card = document.querySelector(`[data-job-id="${CSS.escape(id)}"]`);
      if (card) {
        card.classList.add("recent-upload");
        card.scrollIntoView({ block: "center" });
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
}
window.addEventListener("popstate", restoreRoute);

function renderCorrectionPicker() {
  const select = $("#correctionFile");
  if (!select) return;
  const old = corrections.jobId || select.value;
  select.replaceChildren(node("option", "", "Choose a recording"));
  select.firstChild.value = "";
  const completed = ui.jobs.filter((j) => j.status === "done"),
    size = ui.statsPageSize || 10;
  ui.statsPage = Math.max(
    1,
    Math.min(ui.statsPage || 1, Math.ceil(completed.length / size) || 1),
  );
  paginateControl(
    "#statsPagination",
    completed.length,
    ui.statsPage,
    size,
    (page, pageSize) => {
      ui.statsPage = page;
      ui.statsPageSize = pageSize;
      renderFileStatistics();
    },
  );
  for (const job of completed.slice(
    (ui.statsPage - 1) * size,
    ui.statsPage * size,
  )) {
    const option = node("option", "", job.filename);
    option.value = job.id;
    select.append(option);
  }
  if (old && [...select.options].some((o) => o.value === old))
    select.value = old;
}
function correctionDraftKey() {
  return `nemotron-review:${corrections.jobId}`;
}
function countCorrection() {
  $("#correctionCount").textContent =
    `${$("#correctionText").value.trim() ? $("#correctionText").value.trim().split(/\s+/u).length : 0} words`;
}
function keepCorrectionDraft() {
  try {
    sessionStorage.setItem(
      correctionDraftKey(),
      JSON.stringify({
        text: $("#correctionText").value,
        revision: corrections.revision,
      }),
    );
  } catch {
    $("#correctionStatus").textContent =
      "Browser draft storage is full. Save or export your text.";
  }
}
async function saveReview() {
  if (!corrections.jobId) return true;
  if (corrections.pending) {
    await corrections.pending;
    return $("#correctionText").value === corrections.savedText
      ? true
      : corrections.conflict
        ? false
        : saveReview();
  }
  const text = $("#correctionText").value;
  if (text === corrections.savedText) {
    sessionStorage.removeItem(correctionDraftKey());
    $("#correctionStatus").textContent = "All changes saved";
    return true;
  }
  if (corrections.conflict) return false;
  const id = corrections.jobId,
    revision = corrections.revision;
  $("#correctionStatus").textContent = "Saving…";
  let success = false;
  corrections.pending = (async () => {
    try {
      const result = await api(
        `/api/jobs/${encodeURIComponent(id)}/correction`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, revision }),
        },
      );
      if (corrections.jobId === id) {
        corrections.revision = result.revision;
        corrections.savedText = text;
        $("#correctionStatus").textContent =
          $("#correctionText").value === text
            ? "All changes saved"
            : "Saving newer edits…";
        if ($("#correctionText").value === text)
          sessionStorage.removeItem(correctionDraftKey());
        else keepCorrectionDraft();
      }
      success = true;
    } catch (error) {
      if (corrections.jobId === id) {
        corrections.conflict = error.message.includes("newer revision");
        $("#correctionStatus").textContent = corrections.conflict
          ? "Another tab saved changes. Export your draft before reloading."
          : "Save pending — your draft is kept in this tab.";
        keepCorrectionDraft();
      }
    }
  })();
  await corrections.pending;
  corrections.pending = null;
  if (success && $("#correctionText").value !== corrections.savedText) {
    clearTimeout(corrections.timer);
    corrections.timer = setTimeout(saveReview, 500);
  }
  return success;
}
async function showCorrections(id, navigate = true) {
  if (corrections.jobId !== id && corrections.jobId && !(await saveReview())) {
    feedback(
      "Save or export your current draft before switching recordings.",
      true,
    );
    return;
  }
  const request = ++corrections.request;
  if (corrections.media) corrections.media.pause();
  if (playback.media) playback.media.pause();
  corrections.jobId = id;
  corrections.conflict = false;
  switchTab("corrections", navigate);
  $("#correctionWorkspace").hidden = false;
  $("#correctionEmpty").hidden = true;
  $("#correctionText").disabled = true;
  $("#correctionStatus").textContent = "Loading…";
  try {
    const [job, review] = await Promise.all([
      api(`/api/jobs/${encodeURIComponent(id)}`),
      api(`/api/jobs/${encodeURIComponent(id)}/correction`),
    ]);
    if (request !== corrections.request) return;
    corrections.filename = job.filename;
    corrections.revision = review.revision;
    corrections.savedText = review.text;
    $("#correctionText").value = review.text;
    $("#correctionText").lang = job.language === "auto" ? "" : job.language;
    $("#correctionTitle").textContent = job.filename;
    $("#correctionStatus").textContent = review.revision
      ? "All changes saved"
      : "Original transcript · start editing";
    try {
      const draft = JSON.parse(
        sessionStorage.getItem(correctionDraftKey()) || "null",
      );
      if (draft && draft.text !== review.text) {
        $("#correctionText").value = draft.text;
        corrections.conflict = draft.revision !== review.revision;
        $("#correctionStatus").textContent = corrections.conflict
          ? "Draft restored; a newer server version also exists. Export your draft before reloading."
          : "Unsaved draft restored";
      }
    } catch {}
    $("#correctionText").disabled = false;
    $("#reviewOriginalText").textContent = review.original_text || review.text;
    resetReviewHistory();
    countCorrection();
    renderCorrectionPicker();
    $("#correctionFile").value = id;
    const video = ["mp4", "webm", "mkv", "mov", "avi"].includes(
        job.filename.split(".").pop().toLowerCase(),
      ),
      media = node(video ? "video" : "audio");
    media.controls = true;
    media.preload = "metadata";
    media.src = `/api/jobs/${encodeURIComponent(id)}/media`;
    media.playbackRate = Number($("#correctionSpeed").value);
    media.setAttribute("aria-label", job.filename);
    $("#correctionMediaMount").replaceChildren(media);
    corrections.media = media;
    media.addEventListener("error", () => {
      $("#correctionStatus").textContent =
        "The browser could not play this recording. Your text remains available.";
    });
  } catch (error) {
    $("#correctionStatus").textContent = error.message;
  }
}
$("#correctionFile").addEventListener("change", (e) => {
  if (e.target.value) showCorrections(e.target.value);
});
$("#correctionText").addEventListener("input", () => {
  if (!corrections.applyingHistory) rememberReview();
  countCorrection();
  keepCorrectionDraft();
  $("#correctionStatus").textContent = corrections.conflict
    ? "Draft kept in this tab; resolve the newer saved version before saving."
    : "Unsaved changes";
  clearTimeout(corrections.timer);
  corrections.timer = setTimeout(saveReview, 1000);
});
$("#saveCorrection").addEventListener("click", saveReview);
$("#rewindCorrection").addEventListener("click", () => {
  if (corrections.media)
    corrections.media.currentTime = Math.max(
      0,
      corrections.media.currentTime - 5,
    );
});
function toggleReview() {
  if (!corrections.media) return;
  corrections.media.paused
    ? corrections.media.play().catch(() => {
        $("#correctionStatus").textContent =
          "Press Play on the recording to start.";
      })
    : corrections.media.pause();
}
$("#toggleCorrection").addEventListener("click", toggleReview);
$("#correctionSpeed").addEventListener("change", () => {
  if (corrections.media)
    corrections.media.playbackRate = Number($("#correctionSpeed").value);
});
async function exportReview(format) {
  if (!corrections.jobId) return;
  const saved = await saveReview(),
    text = $("#correctionText").value;
  const contents =
    format === "txt"
      ? text
      : JSON.stringify(
          {
            original_filename: corrections.filename,
            text,
            revision: corrections.revision,
            saved,
            timing_source: null,
          },
          null,
          2,
        );
  const url = URL.createObjectURL(
      new Blob([contents], {
        type:
          format === "txt"
            ? "text/plain;charset=utf-8"
            : "application/json;charset=utf-8",
      }),
    ),
    link = node("a");
  link.href = url;
  link.download =
    corrections.filename.replace(/\.[^.]+$/u, "").slice(0, 90) +
    `-corrected.${format}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (!saved)
    $("#correctionStatus").textContent =
      "Draft exported. Server save is still pending.";
}
$("#exportCorrection").addEventListener("click", () => exportReview("txt"));
$("#exportCorrectionJson").addEventListener("click", () =>
  exportReview("json"),
);
window.addEventListener("keydown", (e) => {
  if ($("#correctionsTab").hidden) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    saveReview();
  }
  if (e.altKey && e.code === "Space") {
    e.preventDefault();
    toggleReview();
  }
});
window.addEventListener("beforeunload", (e) => {
  if (
    corrections.jobId &&
    $("#correctionText").value !== corrections.savedText
  ) {
    keepCorrectionDraft();
    e.preventDefault();
    e.returnValue = "";
  }
});
restoreRoute();
for (const button of document.querySelectorAll("[data-file-status]"))
  button.addEventListener("click", () => {
    ui.fileStatus = button.dataset.fileStatus;
    ui.filePage = 1;
    renderJobs();
    if (location.pathname === "/files") {
      const url = new URL(location.href);
      url.searchParams.set("status", ui.fileStatus);
      history.replaceState({}, "", url);
    }
  });
$("#fileSort").addEventListener("change", () => {
  renderJobs();
  if (location.pathname === "/files") {
    const url = new URL(location.href);
    url.searchParams.set("sort", $("#fileSort").value);
    history.replaceState({}, "", url);
  }
});
if (location.pathname === "/files") {
  const params = new URLSearchParams(location.search);
  ui.fileStatus = ["all", "queued", "processing", "done", "failed"].includes(
    params.get("status"),
  )
    ? params.get("status")
    : "all";
  if (["newest", "oldest", "status", "name"].includes(params.get("sort")))
    $("#fileSort").value = params.get("sort");
}

function paginateControl(target, total, page, size, change) {
  const holder = $(target);
  if (!holder) return;
  holder.replaceChildren();
  const label = node("label", "pagination-size", "Rows per page "),
    select = node("select");
  select.setAttribute(
    "aria-label",
    target === "#filePagination"
      ? "Files rows per page"
      : "Statistics rows per page",
  );
  for (const value of [10, 25, 50]) {
    const option = node("option", "", String(value));
    option.value = value;
    select.append(option);
  }
  select.value = size;
  select.addEventListener("change", () => change(1, Number(select.value)));
  label.append(select);
  const info = node(
      "span",
      "",
      total
        ? `${(page - 1) * size + 1}–${Math.min(page * size, total)} of ${total}`
        : "0 files",
    ),
    prev = node("button", "secondary", "‹"),
    next = node("button", "secondary", "›");
  prev.setAttribute("aria-label", "Previous page");
  next.setAttribute("aria-label", "Next page");
  prev.disabled = page <= 1;
  next.disabled = page * size >= total;
  prev.addEventListener("click", () => change(page - 1, size));
  next.addEventListener("click", () => change(page + 1, size));
  const controls = node("div");
  controls.append(info, prev, next);
  holder.append(label, controls);
}

function resetReviewHistory() {
  corrections.history = [
    { text: $("#correctionText").value, start: 0, end: 0 },
  ];
  corrections.historyIndex = 0;
  corrections.lastHistoryTime = 0;
  updateReviewHistoryButtons();
}
function updateReviewHistoryButtons() {
  $("#reviewUndo").disabled = !(corrections.historyIndex > 0);
  $("#reviewRedo").disabled = !(
    corrections.history &&
    corrections.historyIndex < corrections.history.length - 1
  );
}
function rememberReview(force = false) {
  const editor = $("#correctionText"),
    entry = {
      text: editor.value,
      start: editor.selectionStart,
      end: editor.selectionEnd,
    };
  corrections.history ??= [entry];
  corrections.historyIndex ??= 0;
  if (corrections.history[corrections.historyIndex]?.text === entry.text)
    return;
  const now = performance.now();
  if (
    !force &&
    corrections.historyIndex > 0 &&
    now - (corrections.lastHistoryTime || 0) < 500
  )
    corrections.history[corrections.historyIndex] = entry;
  else {
    corrections.history = corrections.history.slice(
      0,
      corrections.historyIndex + 1,
    );
    corrections.history.push(entry);
    if (corrections.history.length > 30) corrections.history.shift();
    corrections.historyIndex = corrections.history.length - 1;
  }
  corrections.lastHistoryTime = now;
  updateReviewHistoryButtons();
}
function changeReviewHistory(delta) {
  const index = corrections.historyIndex + delta;
  if (!corrections.history || index < 0 || index >= corrections.history.length)
    return;
  corrections.historyIndex = index;
  const entry = corrections.history[index],
    editor = $("#correctionText");
  corrections.applyingHistory = true;
  editor.value = entry.text;
  editor.setSelectionRange(entry.start, entry.end);
  editor.dispatchEvent(new Event("input", { bubbles: true }));
  corrections.applyingHistory = false;
  editor.focus();
  updateReviewHistoryButtons();
}
$("#reviewUndo").addEventListener("click", () => changeReviewHistory(-1));
$("#reviewRedo").addEventListener("click", () => changeReviewHistory(1));
function editedReview() {
  corrections.lastHistoryTime = 0;
  $("#correctionText").dispatchEvent(new Event("input", { bubbles: true }));
}
function findReview(direction = 1) {
  const editor = $("#correctionText"),
    query = $("#reviewFindText").value;
  if (!query) {
    $("#reviewFindCount").textContent = "";
    return;
  }
  const text = editor.value.toLocaleLowerCase(),
    needle = query.toLocaleLowerCase(),
    matches = [];
  for (
    let from = 0;
    (from = text.indexOf(needle, from)) >= 0;
    from += Math.max(1, needle.length)
  )
    matches.push(from);
  if (!matches.length) {
    $("#reviewFindCount").textContent = "No matches";
    return;
  }
  let position =
    direction >= 0
      ? matches.findIndex((i) => i >= editor.selectionEnd)
      : matches.findLastIndex((i) => i < editor.selectionStart);
  if (position < 0) position = direction >= 0 ? 0 : matches.length - 1;
  editor.focus();
  editor.setSelectionRange(matches[position], matches[position] + query.length);
  $("#reviewFindCount").textContent = `${position + 1} of ${matches.length}`;
}
$("#toggleReviewFind").addEventListener("click", () => {
  $("#reviewFindPanel").hidden = !$("#reviewFindPanel").hidden;
  if (!$("#reviewFindPanel").hidden) $("#reviewFindText").focus();
});
$("#closeReviewFind").addEventListener("click", () => {
  $("#reviewFindPanel").hidden = true;
  $("#correctionText").focus();
});
$("#reviewFindNext").addEventListener("click", () => findReview(1));
$("#reviewFindPrev").addEventListener("click", () => findReview(-1));
$("#reviewFindText").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    findReview(e.shiftKey ? -1 : 1);
  }
});
$("#reviewReplaceOne").addEventListener("click", () => {
  const editor = $("#correctionText"),
    query = $("#reviewFindText").value;
  if (!query) return;
  if (
    editor.value
      .slice(editor.selectionStart, editor.selectionEnd)
      .toLocaleLowerCase() !== query.toLocaleLowerCase()
  ) {
    findReview();
    return;
  }
  editor.setRangeText(
    $("#reviewReplaceText").value,
    editor.selectionStart,
    editor.selectionEnd,
    "end",
  );
  editedReview();
  findReview();
});
$("#reviewReplaceAll").addEventListener("click", () => {
  const editor = $("#correctionText"),
    query = $("#reviewFindText").value;
  if (!query) return;
  const lower = editor.value.toLocaleLowerCase(),
    needle = query.toLocaleLowerCase();
  let result = "",
    last = 0,
    count = 0;
  for (
    let position = lower.indexOf(needle);
    position >= 0;
    position = lower.indexOf(needle, last)
  ) {
    result +=
      editor.value.slice(last, position) + $("#reviewReplaceText").value;
    last = position + query.length;
    count++;
  }
  if (!count) {
    $("#reviewFindCount").textContent = "No matches";
    return;
  }
  editor.value = result + editor.value.slice(last);
  editedReview();
  $("#reviewFindCount").textContent = `Replaced ${count} matches`;
});
$("#insertReviewTime").addEventListener("click", () => {
  if (!corrections.media) return;
  const editor = $("#correctionText");
  editor.setRangeText(
    `[${clock(corrections.media.currentTime)}] `,
    editor.selectionStart,
    editor.selectionEnd,
    "end",
  );
  editedReview();
  editor.focus();
});
$("#checkReviewNumbers").addEventListener("click", () => {
  const match =
    /(?:punct(?:e)?|virgulă)\s+(?:zero\s*){2,}|(?:zero\s+){2,}zero/iu.exec(
      $("#correctionText").value,
    );
  if (!match) {
    $("#correctionStatus").textContent =
      "No suspicious zero/separator phrases found. Verify important amounts by listening.";
    return;
  }
  $("#reviewFindPanel").hidden = false;
  $("#reviewFindText").value = match[0];
  $("#correctionText").setSelectionRange(
    match.index,
    match.index + match[0].length,
  );
  $("#correctionText").focus();
  $("#reviewFindCount").textContent = "Check this amount against the recording";
});
window.addEventListener("keydown", (e) => {
  if ($("#correctionsTab").hidden) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
    e.preventDefault();
    $("#reviewFindPanel").hidden = false;
    $("#reviewFindText").focus();
  }
  if (
    (e.ctrlKey || e.metaKey) &&
    e.key.toLowerCase() === "z" &&
    document.activeElement === $("#correctionText")
  ) {
    e.preventDefault();
    changeReviewHistory(e.shiftKey ? 1 : -1);
  }
  if (
    e.altKey &&
    ["ArrowLeft", "ArrowRight"].includes(e.key) &&
    corrections.media
  ) {
    e.preventDefault();
    corrections.media.currentTime = Math.max(
      0,
      corrections.media.currentTime + (e.key === "ArrowLeft" ? -5 : 5),
    );
  }
});
