/* Analyst tools stay beside the recording and inside the contextual menu. */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const make = (tag, className = "", text = "") => {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = text;
    return element;
  };
  const state = {
    review: null,
    player: null,
    context: null,
    cache: new Map(),
    operations: Promise.resolve(),
  };
  async function request(path, options) {
    const response = await fetch(path, options);
    const body = await response.json();
    if (!response.ok)
      throw Error(body.detail || "This action could not be completed.");
    return body;
  }
  const json = (method, body) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const path = (id, suffix) => `/api/jobs/${encodeURIComponent(id)}/${suffix}`;
  const clock = (time) => {
    if (!Number.isFinite(time)) return "—";
    const seconds = Math.max(0, time),
      minutes = Math.floor(seconds / 60);
    return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  };
  function announce(message, error = false) {
    const status = $("#reviewToolsMessage") || $("#reviewSyncStatus");
    if (status) {
      status.textContent = message;
      status.classList.toggle("review-tools-error", error);
    }
  }
  function speakerName(speaker, result) {
    return result?.speaker_names?.[String(speaker)] || `Speaker ${speaker}`;
  }
  function resolveSpeaker(speaker, value) {
    if (speaker === undefined || speaker === null) return null;
    let current = String(speaker);
    for (let n = 0; n < 129 && value.merges?.[current] !== undefined; n++)
      current = String(value.merges[current]);
    return current;
  }
  function effectiveSpeaker(speaker, start, end, value) {
    for (const override of value.overrides || [])
      if (Math.min(end, override.end) > Math.max(start, override.start))
        speaker = override.speaker;
    return resolveSpeaker(speaker, value);
  }
  function applyResult(result, metadata) {
    const value = metadata?.value || { names: {}, merges: {}, overrides: [] };
    for (const segment of result.segments || [])
      for (const key of ["words", "playback_words"])
        for (const word of segment[key] || []) {
          if (!Number.isFinite(word.start) || !Number.isFinite(word.end))
            continue;
          // Keep the model identity on the presentation object so refreshing names/merges is reversible.
          if (!Object.prototype.hasOwnProperty.call(word, "model_speaker"))
            word.model_speaker = word.speaker;
          const speaker = effectiveSpeaker(
            word.model_speaker,
            segment.start_seconds + word.start,
            segment.start_seconds + word.end,
            value,
          );
          if (speaker !== null) word.speaker = Number(speaker);
          word.speaker_name = value.names?.[speaker] || `Speaker ${speaker}`;
        }
    result.speaker_names = value.names || {};
    return result;
  }
  async function prepareResult(result) {
    if (!result?.job_id) return result;
    try {
      const metadata = await request(path(result.job_id, "review-tools"));
      state.cache.set(result.job_id, metadata);
      applyResult(result, metadata);
    } catch (error) {
      announce(`Speaker edits unavailable: ${error.message}`, true);
    }
    return result;
  }
  function rangeReviewed(range, metadata) {
    return (
      !!range &&
      (metadata?.value.reviewed || []).some(
        (item) =>
          item.start <= range.start + 0.02 && item.end >= range.end - 0.02,
      )
    );
  }
  function decorateWords() {
    const data = window.ReviewSync?.getState();
    if (!data || data.jobId !== state.review?.id) return;
    for (let index = 0; index < data.words.length; index++) {
      const word = data.words[index];
      data.spans[index]?.classList.toggle(
        "review-verified-word",
        rangeReviewed(word, state.review.metadata),
      );
    }
  }
  function updatePlayers(id, metadata) {
    state.cache.set(id, metadata);
    for (const session of [state.review, state.player])
      if (session?.id === id) {
        session.metadata = metadata;
        updatePanel(session);
      }
    decorateWords();
    if (
      typeof playback !== "undefined" &&
      playback.jobId === id &&
      playback.result
    ) {
      applyResult(playback.result, metadata);
      renderSyncedTranscript(playback.result, true);
    }
  }
  function mutate(id, operation) {
    const pending = state.operations
      .catch(() => {})
      .then(async () => {
        const metadata =
          state.cache.get(id) || (await request(path(id, "review-tools")));
        const value = structuredClone(metadata.value);
        operation(value);
        const saved = await request(
          path(id, "review-tools"),
          json("PUT", { revision: metadata.revision, value }),
        );
        updatePlayers(id, saved);
        announce("Review changes saved.");
        return saved;
      });
    state.operations = pending;
    pending.catch((error) => announce(error.message, true));
    return pending;
  }
  function inputNumber(label) {
    const wrapper = make("label", "review-range-label", label),
      input = make("input");
    input.type = "number";
    input.min = "0";
    input.step = "0.01";
    input.setAttribute("aria-label", label);
    wrapper.append(input);
    return { wrapper, input };
  }
  function button(label, callback, className = "secondary") {
    const control = make("button", className, label);
    control.type = "button";
    control.addEventListener("click", callback);
    return control;
  }
  function stopLoop(session) {
    session.loop = null;
    session.stopAt = null;
    if (session.kind === "review") window.ReviewSync?.stopLoop();
    session.loopButton.textContent = "Loop";
  }
  function setRange(session, range, source = "waveform") {
    if (
      !range ||
      !Number.isFinite(range.start) ||
      !Number.isFinite(range.end) ||
      range.end <= range.start
    )
      return;
    session.range = { ...range };
    session.rangeSource = source;
    session.startInput.value = range.start.toFixed(2);
    session.endInput.value = range.end.toFixed(2);
    session.rangeControls.hidden = false;
    session.rangeNote.textContent = `${clock(range.start)} – ${clock(range.end)} · ${(range.end - range.start).toFixed(1)}s`;
    drawWave(session);
  }
  function playRange(session, loop = false) {
    const range = session.range;
    if (!range) return;
    stopLoop(session);
    if (loop) session.loop = { ...range };
    session.stopAt = loop ? null : range.end;
    session.loopButton.textContent = loop ? "Stop loop" : "Loop";
    session.media.currentTime = range.start;
    session.media
      .play()
      .catch(() => announce("Press Play on the recording to begin.", true));
  }
  function downloadText(text, extension = "txt") {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" }),
      url = URL.createObjectURL(blob),
      link = make("a");
    link.href = url;
    link.download = `selection.${extension}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function downloadAudio(session, range) {
    if (!range) return;
    try {
      announce("Preparing the selected audio…");
      const response = await fetch(
        path(session.id, `clip?start=${range.start}&end=${range.end}`),
      );
      if (!response.ok)
        throw Error((await response.json()).detail || "Audio export failed.");
      const url = URL.createObjectURL(await response.blob()),
        link = make("a");
      link.href = url;
      link.download = "selection.wav";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      announce("Selected audio exported.");
    } catch (error) {
      announce(error.message, true);
    }
  }
  function drawWave(session) {
    if (!session.canvas?.isConnected || !session.waveform) return;
    const width = Math.max(1, session.canvas.clientWidth),
      height = session.canvas.clientHeight,
      dpr = window.devicePixelRatio || 1;
    if (
      session.canvas.width !== Math.round(width * dpr) ||
      session.canvas.height !== Math.round(height * dpr)
    ) {
      session.canvas.width = Math.round(width * dpr);
      session.canvas.height = Math.round(height * dpr);
    }
    const context = session.canvas.getContext("2d");
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);
    const peaks = session.waveform.peaks,
      duration = session.waveform.duration_s;
    const peak = Math.max(0.03, ...peaks),
      mid = height / 2;
    if (session.range) {
      context.fillStyle = "#0071e31c";
      context.fillRect(
        (session.range.start / duration) * width,
        0,
        ((session.range.end - session.range.start) / duration) * width,
        height,
      );
    }
    for (const reviewed of session.metadata?.value.reviewed || []) {
      context.fillStyle = "#27844a12";
      context.fillRect(
        (reviewed.start / duration) * width,
        0,
        ((reviewed.end - reviewed.start) / duration) * width,
        height,
      );
    }
    context.strokeStyle = "#8c9bad";
    context.lineWidth = 1;
    context.beginPath();
    for (let pixel = 0; pixel < width; pixel += 2) {
      const first = Math.floor((pixel / width) * peaks.length),
        last = Math.max(
          first + 1,
          Math.ceil(((pixel + 2) / width) * peaks.length),
        );
      let amplitude = 0;
      for (let index = first; index < Math.min(last, peaks.length); index++)
        amplitude = Math.max(amplitude, peaks[index]);
      const extent = Math.max(0.5, (amplitude / peak) * (mid - 7));
      context.moveTo(pixel, mid - extent);
      context.lineTo(pixel, mid + extent);
    }
    context.stroke();
    context.strokeStyle = "#0071e3";
    context.lineWidth = 1.5;
    const position = Math.max(
      0,
      Math.min(width, (session.media.currentTime / duration) * width),
    );
    context.beginPath();
    context.moveTo(position, 0);
    context.lineTo(position, height);
    context.stroke();
  }
  function updatePanel(session) {
    const metadata = session.metadata;
    if (!metadata) return;
    session.progress.textContent = `${metadata.progress.percent}% audio reviewed`;
    session.progressBar.value = metadata.progress.percent;
    const previousMergeFrom = session.mergeFrom.value,
      previousMergeTo = session.mergeTo.value;
    session.speakerList.replaceChildren();
    session.mergeFrom.replaceChildren();
    session.mergeTo.replaceChildren();
    for (const speaker of metadata.speakers) {
      const row = make("div", "review-speaker-row"),
        name = make("input");
      name.type = "text";
      name.value = speaker.name;
      name.maxLength = 100;
      name.setAttribute("aria-label", `Name for speaker ${speaker.id}`);
      const save = button("Save", () => {
        const enteredName = name.value.trim();
        if (!enteredName) {
          announce("Enter a speaker name before saving.", true);
          return;
        }
        mutate(session.id, (value) => {
          value.names[speaker.id] = enteredName;
        });
      });
      row.append(make("span", "", `Speaker ${speaker.id}`), name, save);
      session.speakerList.append(row);
      for (const select of [session.mergeFrom, session.mergeTo]) {
        const option = make("option", "", speaker.name);
        option.value = speaker.id;
        select.append(option);
      }
    }
    if (!metadata.speakers.length)
      session.speakerList.append(
        make(
          "p",
          "review-tools-hint",
          "No speaker labels yet. Select timed words and use Assign speaker to add one.",
        ),
      );
    if (
      [...session.mergeFrom.options].some(
        (option) => option.value === previousMergeFrom,
      )
    )
      session.mergeFrom.value = previousMergeFrom;
    if (
      [...session.mergeTo.options].some(
        (option) => option.value === previousMergeTo,
      )
    )
      session.mergeTo.value = previousMergeTo;
    else if (session.mergeTo.options.length > 1)
      session.mergeTo.selectedIndex = 1;
    session.mergeButton.disabled = metadata.speakers.length < 2;
    session.speakerHint.textContent =
      metadata.speaker_scope && metadata.speaker_scope !== "file"
        ? "Legacy speaker IDs may be local to chunks. Retry with speaker identification for file-wide labels."
        : "Speaker changes affect this recording only. The model output stays intact.";
    drawWave(session);
  }
  function nextUnreviewed(session) {
    const mapped =
      session.kind === "review"
        ? window.ReviewSync?.getState().words
        : typeof playback !== "undefined"
          ? playback.words
          : [];
    const words = [...(mapped || [])].sort((a, b) => a.start - b.start);
    const candidates = words.filter(
      (word) => !rangeReviewed(word, session.metadata),
    );
    const next =
      candidates.find(
        (word) => word.start > session.media.currentTime + 0.05,
      ) || candidates[0];
    if (next) {
      session.media.currentTime = next.start;
      announce("Moved to the next unreviewed timed word.");
    } else
      announce(
        "No unreviewed timed words remain. Check any text without timing separately.",
      );
  }
  function createPanel(id, media, kind) {
    const mount =
      kind === "review" ? $("#correctionMediaMount") : $("#mediaMount");
    const previous = $(
      kind === "review" ? "#reviewWaveTools" : "#playerWaveTools",
    );
    previous?.remove();
    const panel = make("section", "review-audio-tools");
    panel.id = kind === "review" ? "reviewWaveTools" : "playerWaveTools";
    const session = {
      id,
      media,
      kind,
      panel,
      range: null,
      loop: null,
      stopAt: null,
      metadata: null,
      waveform: null,
    };
    const header = make("div", "review-wave-heading"),
      canvas = make("canvas", "review-wave-canvas"),
      note = make("p", "review-wave-note", "Loading the recording waveform…");
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "slider");
    canvas.setAttribute(
      "aria-label",
      "Recording waveform. Click to seek or drag to select audio.",
    );
    canvas.setAttribute("aria-valuemin", "0");
    header.append(
      make("span", "", "Recording waveform"),
      button(
        "Clear range",
        () => {
          session.range = null;
          stopLoop(session);
          session.rangeControls.hidden = true;
          drawWave(session);
        },
        "review-quiet-button",
      ),
    );
    const controls = make("div", "review-wave-range");
    controls.hidden = true;
    const rangeNote = make("span", "review-range-note");
    const loopButton = button("Loop", () => {
      if (session.loop) stopLoop(session);
      else playRange(session, true);
    });
    controls.append(
      rangeNote,
      button("Play range", () => playRange(session)),
      loopButton,
      button("Export audio", () => downloadAudio(session, session.range)),
    );
    const progressRow = make("div", "review-progress-row"),
      progress = make("span", "", "0% audio reviewed"),
      bar = make("progress");
    bar.max = 100;
    bar.value = 0;
    bar.setAttribute("aria-label", "Audio review progress");
    progressRow.append(
      progress,
      button(
        "Next unreviewed",
        () => nextUnreviewed(session),
        "review-quiet-button",
      ),
    );
    const details = make("details", "review-advanced"),
      summary = make("summary", "", "Speakers & review tools"),
      body = make("div", "review-advanced-body");
    const speakerList = make("div", "review-speaker-list"),
      speakerHint = make("p", "review-tools-hint");
    const merge = make("div", "review-merge-row"),
      mergeFrom = make("select"),
      mergeTo = make("select");
    mergeFrom.setAttribute("aria-label", "Speaker to merge");
    mergeTo.setAttribute("aria-label", "Keep speaker");
    const mergeButton = button("Merge", () => {
      const source = mergeFrom.value,
        target = mergeTo.value;
      if (source && target && source !== target)
        mutate(id, (value) => {
          value.merges[source] = target;
        });
    });
    merge.append(mergeFrom, make("span", "", "→"), mergeTo, mergeButton);
    const manual = make("div", "review-manual-range"),
      start = inputNumber("From (seconds)"),
      end = inputNumber("To (seconds)");
    manual.append(
      start.wrapper,
      end.wrapper,
      button("Select range", () => {
        const range = {
          start: Number(start.input.value),
          end: Number(end.input.value),
        };
        if (
          !Number.isFinite(range.start) ||
          !Number.isFinite(range.end) ||
          range.start < 0 ||
          range.end <= range.start ||
          range.end > media.duration
        ) {
          announce("Choose a range inside the recording.", true);
          return;
        }
        setRange(session, range);
      }),
    );
    const actions = make("div", "review-advanced-actions");
    actions.append(
      button("Mark range reviewed", () => {
        if (session.range)
          mutate(id, (value) => value.reviewed.push({ ...session.range }));
        else
          announce(
            "Drag across the waveform or select transcript words first.",
          );
      }),
      button("Undo last assignment", () =>
        mutate(id, (value) => value.overrides.pop()),
      ),
    );
    body.append(speakerList, merge, speakerHint, manual, actions);
    details.append(summary, body);
    panel.append(header, canvas, note, controls, progressRow, bar, details);
    if (kind === "review") {
      const status = make("p", "review-tools-message");
      status.id = "reviewToolsMessage";
      status.setAttribute("role", "status");
      panel.append(status);
    }
    mount.insertAdjacentElement("afterend", panel);
    Object.assign(session, {
      canvas,
      note,
      rangeControls: controls,
      rangeNote,
      loopButton,
      progress,
      progressBar: bar,
      speakerList,
      speakerHint,
      mergeFrom,
      mergeTo,
      mergeButton,
      startInput: start.input,
      endInput: end.input,
    });
    let pointer = null;
    const timeAt = (x) =>
      Math.max(
        0,
        Math.min(
          session.waveform?.duration_s || 0,
          ((x - canvas.getBoundingClientRect().left) / canvas.clientWidth) *
            (session.waveform?.duration_s || 0),
        ),
      );
    canvas.addEventListener("pointerdown", (event) => {
      if (!session.waveform) return;
      pointer = { x: event.clientX, start: timeAt(event.clientX) };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      if (pointer && Math.abs(event.clientX - pointer.x) > 3) {
        const current = timeAt(event.clientX);
        setRange(session, {
          start: Math.min(pointer.start, current),
          end: Math.max(pointer.start, current),
        });
      }
    });
    canvas.addEventListener("pointerup", (event) => {
      if (!pointer) return;
      if (Math.abs(event.clientX - pointer.x) <= 3) {
        stopLoop(session);
        media.currentTime = pointer.start;
      }
      pointer = null;
    });
    canvas.addEventListener("pointercancel", () => {
      pointer = null;
    });
    canvas.addEventListener("keydown", (event) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        stopLoop(session);
        media.currentTime =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? media.duration
              : Math.max(
                  0,
                  Math.min(
                    media.duration,
                    media.currentTime + (event.key === "ArrowLeft" ? -5 : 5),
                  ),
                );
      }
    });
    const tick = () => {
      if (
        session.loop &&
        !media.paused &&
        media.currentTime >= session.loop.end
      )
        media.currentTime = session.loop.start;
      else if (
        session.stopAt !== null &&
        !media.paused &&
        media.currentTime >= session.stopAt
      ) {
        media.pause();
        session.stopAt = null;
      }
      canvas.setAttribute(
        "aria-valuenow",
        String(Number(media.currentTime.toFixed(2))),
      );
      canvas.setAttribute("aria-valuetext", clock(media.currentTime));
      drawWave(session);
    };
    let frame;
    const animate = () => {
      tick();
      if (!media.paused && !media.ended && state[kind] === session)
        frame = requestAnimationFrame(animate);
    };
    media.addEventListener("play", () => {
      cancelAnimationFrame(frame);
      animate();
    });
    media.addEventListener("pause", () => cancelAnimationFrame(frame));
    media.addEventListener("seeking", () => {
      if (
        session.loop &&
        (media.currentTime < session.loop.start - 0.1 ||
          media.currentTime > session.loop.end + 0.1)
      )
        stopLoop(session);
    });
    media.addEventListener("timeupdate", tick);
    media.addEventListener("seeked", tick);
    media.addEventListener("loadedmetadata", () => {
      canvas.setAttribute("aria-valuemax", String(media.duration));
      tick();
    });
    const resize = new ResizeObserver(() => drawWave(session));
    resize.observe(canvas);
    session.resize = resize;
    return session;
  }
  async function load(id, media, kind) {
    if (!id || !media) return;
    const previous = state[kind];
    if (
      previous?.id === id &&
      previous.media === media &&
      previous.panel.isConnected
    )
      return;
    previous?.resize.disconnect();
    const session = createPanel(id, media, kind);
    state[kind] = session;
    try {
      const metadata = await request(path(id, "review-tools"));
      if (state[kind] !== session) return;
      session.metadata = metadata;
      state.cache.set(id, metadata);
      updatePanel(session);
      decorateWords();
    } catch (error) {
      announce(error.message, true);
    }
    try {
      const waveform = await request(path(id, "waveform?bins=1600"));
      if (state[kind] !== session) return;
      session.waveform = waveform;
      session.note.textContent = "Click to seek · drag to select a range";
      drawWave(session);
    } catch (error) {
      session.note.textContent = `Waveform unavailable: ${error.message}`;
    }
  }
  function closeMenu() {
    window.ReviewSync?.closeMenu();
  }
  function contextSession() {
    return state.review?.id === state.context?.jobId ? state.review : null;
  }
  function showAssignment() {
    const session = contextSession(),
      selection = state.context;
    if (!session || !selection?.range) return;
    const dialog = make("dialog", "review-speaker-dialog"),
      form = make("form"),
      title = make("h2", "", "Assign speaker"),
      note = make(
        "p",
        "review-tools-hint",
        `${selection.word_count} words · ${clock(selection.range.start)}–${clock(selection.range.end)}`,
      ),
      select = make("select"),
      label = make("label", "", "Speaker"),
      nameLabel = make("label", "", "New speaker name"),
      name = make("input"),
      status = make("p", "review-tools-message"),
      actions = make("div", "review-dialog-actions");
    name.type = "text";
    name.maxLength = 100;
    name.placeholder = "For example: Ana";
    nameLabel.append(name);
    select.setAttribute("aria-label", "Assign selected words to speaker");
    for (const speaker of session.metadata?.speakers || []) {
      const option = make("option", "", speaker.name);
      option.value = speaker.id;
      select.append(option);
    }
    const add = make("option", "", "New speaker (split selection)");
    add.value = "new";
    select.append(add);
    label.append(select);
    const refresh = () => {
      nameLabel.hidden = select.value !== "new";
    };
    select.addEventListener("change", refresh);
    refresh();
    const save = make("button", "primary compact", "Assign");
    save.type = "submit";
    actions.append(
      button("Cancel", () => dialog.close()),
      save,
    );
    form.append(title, note, label, nameLabel, status, actions);
    dialog.append(form);
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      save.disabled = true;
      const selectedSpeaker = select.value,
        enteredName = name.value.trim();
      try {
        await mutate(session.id, (value) => {
          let id = selectedSpeaker;
          if (id === "new") {
            const ids = [
              ...(session.metadata?.speakers || []).map((speaker) =>
                Number(speaker.id),
              ),
              ...Object.keys(value.names).map(Number),
              ...value.overrides.map((item) => Number(item.speaker)),
            ];
            id = String(Math.max(0, ...ids) + 1);
            value.names[id] = enteredName || `Speaker ${id}`;
          }
          value.overrides.push({ ...selection.range, speaker: id });
        });
        dialog.close();
      } catch (error) {
        status.textContent = error.message;
      } finally {
        save.disabled = false;
      }
    });
    closeMenu();
    dialog.showModal();
  }
  function removeReviewed(ranges, selection) {
    return ranges.flatMap((range) => {
      if (range.end <= selection.start || range.start >= selection.end)
        return [range];
      const pieces = [];
      if (range.start < selection.start)
        pieces.push({ start: range.start, end: selection.start });
      if (range.end > selection.end)
        pieces.push({ start: selection.end, end: range.end });
      return pieces;
    });
  }
  async function realignSelection() {
    const session = contextSession(),
      selection = state.context,
      range =
        session?.rangeSource === "waveform"
          ? session.range
          : selection?.range || session?.range;
    closeMenu();
    if (!session || !selection?.text || !range) return;
    if (typeof saveReview === "function" && !(await saveReview())) {
      announce("Save the transcript before realigning this selection.", true);
      return;
    }
    try {
      const task = await request(
        path(session.id, "correction/realign"),
        json("POST", {
          text: selection.documentText,
          revision: corrections.revision,
          char_start: selection.char_start,
          char_end: selection.char_end,
          start_seconds: range.start,
          end_seconds: range.end,
        }),
      );
      announce(
        "Selection realignment queued. It will run when a worker is available.",
      );
      let count = 0;
      const poll = async () => {
        if (state.review?.id !== session.id || count++ > 450) return;
        try {
          const result = await request(
            `/api/alignments/${encodeURIComponent(task.id || task.task_id)}`,
          );
          if (result.status === "done" || result.status === "completed") {
            announce("Selection realigned from the recording.");
            window.ReviewSync?.refresh();
            return;
          }
          if (result.status === "failed") {
            announce(result.error || "Selection realignment failed.", true);
            return;
          }
          setTimeout(poll, 2000);
        } catch (error) {
          announce(error.message, true);
        }
      };
      setTimeout(poll, 1500);
    } catch (error) {
      announce(error.message, true);
    }
  }
  function setupMenu() {
    const menu = $("#reviewContextMenu"),
      note = $("#reviewContextNote");
    if (!menu || $("#reviewContextLoop")) return;
    const loop = button(
      "Loop selection",
      () => {
        const session = contextSession();
        if (session && state.context?.range) {
          setRange(session, state.context.range, "transcript");
          playRange(session, true);
        }
        closeMenu();
      },
      "",
    );
    loop.id = "reviewContextLoop";
    loop.setAttribute("role", "menuitem");
    const mark = button(
      "Mark selection reviewed",
      () => {
        const session = contextSession(),
          range = state.context?.range;
        if (session && range)
          mutate(session.id, (value) => {
            value.reviewed = rangeReviewed(range, session.metadata)
              ? removeReviewed(value.reviewed, range)
              : [...value.reviewed, { ...range }];
          });
        closeMenu();
      },
      "",
    );
    mark.id = "reviewContextReviewed";
    mark.setAttribute("role", "menuitem");
    const assign = button("Assign speaker…", showAssignment, "");
    assign.id = "reviewContextSpeaker";
    assign.setAttribute("role", "menuitem");
    const realign = button("Realign selection", realignSelection, "");
    realign.id = "reviewContextRealign";
    realign.setAttribute("role", "menuitem");
    const exports = make("details", "review-context-exports"),
      summary = make("summary", "", "Export selection"),
      list = make("div");
    exports.append(summary, list);
    const exportText = button(
      "Plain text (.txt)",
      () => {
        downloadText(state.context?.text || "");
        closeMenu();
      },
      "",
    );
    exportText.id = "reviewContextExportText";
    const exportAudio = button(
      "Audio excerpt (.wav)",
      () => {
        const session = contextSession();
        if (session) downloadAudio(session, state.context.range);
        closeMenu();
      },
      "",
    );
    exportAudio.id = "reviewContextExportAudio";
    list.append(exportText, exportAudio);
    for (const format of ["docx", "pdf"]) {
      const control = button(
        format === "docx" ? "Word document (.docx)" : "PDF document (.pdf)",
        async () => {
          const session = contextSession();
          if (!session) return;
          try {
            const response = await fetch(
              path(session.id, `export/${format}`),
              json("POST", { text: state.context.text, source: "review" }),
            );
            if (!response.ok)
              throw Error(
                (await response.json()).detail || "Selection export failed.",
              );
            const url = URL.createObjectURL(await response.blob()),
              link = make("a");
            link.href = url;
            link.download = `selection.${format}`;
            link.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          } catch (error) {
            announce(error.message, true);
          }
          closeMenu();
        },
        "",
      );
      control.id = `reviewContextExport${format.toUpperCase()}`;
      list.append(control);
    }
    for (const control of list.querySelectorAll("button"))
      control.setAttribute("role", "menuitem");
    menu.insertBefore(loop, note);
    menu.insertBefore(mark, note);
    menu.insertBefore(assign, note);
    menu.insertBefore(realign, note);
    menu.insertBefore(exports, note);
    document.addEventListener("review-context", (event) => {
      state.context = event.detail;
      const session = contextSession(),
        timed = !!event.detail.range;
      loop.disabled = !timed;
      loop.textContent = event.detail.selected
        ? "Loop selection"
        : "Loop this word";
      assign.disabled = !timed;
      mark.disabled = !timed;
      realign.disabled = !event.detail.text || !(timed || session?.range);
      realign.textContent =
        session?.range && session.rangeSource === "waveform"
          ? "Realign to selected audio range"
          : "Realign selection";
      exportAudio.disabled = !timed;
      exports.hidden = !event.detail.text;
      for (const control of list.querySelectorAll("button"))
        if (control !== exportAudio) control.disabled = !event.detail.text;
      mark.textContent = rangeReviewed(event.detail.range, session?.metadata)
        ? "Mark selection unreviewed"
        : "Mark selection reviewed";
      exports.open = false;
      if (event.detail.selected && event.detail.word_count > 1)
        summary.textContent = `Export ${event.detail.word_count} selected words`;
      else summary.textContent = "Export selected text";
    });
  }
  window.ReviewTools = {
    prepareResult,
    applyResult,
    speakerName,
    loadReview: (id, media) => load(id, media, "review"),
    loadPlayer: (id, media) => load(id, media, "player"),
    getSelection: () => state.context || window.ReviewSync?.getSelection(),
  };
  setupMenu();
  document.addEventListener("review-loaded", (event) =>
    load(event.detail.id, event.detail.media, "review"),
  );
  document.addEventListener("player-loaded", (event) =>
    load(event.detail.id, event.detail.media, "player"),
  );
  window.addEventListener("review-loaded", (event) =>
    load(event.detail.id, event.detail.media, "review"),
  );
  window.addEventListener("player-loaded", (event) =>
    load(event.detail.id, event.detail.media, "player"),
  );
  document.addEventListener("review-alignment", decorateWords);
  document.addEventListener("review-audio-reset", () => {
    if (state.review) stopLoop(state.review);
  });
  if (
    typeof corrections !== "undefined" &&
    corrections.jobId &&
    corrections.media
  )
    load(corrections.jobId, corrections.media, "review");
  if (typeof playback !== "undefined" && playback.jobId && playback.media)
    load(playback.jobId, playback.media, "player");
})();
