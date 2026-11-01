/* Editable transcript with an accessible textarea and a synchronized visual layer. */
(() => {
  const editor = document.querySelector("#correctionText");
  const layer = document.querySelector("#reviewHighlightLayer");
  const status = document.querySelector("#reviewSyncStatus");
  const menu = document.querySelector("#reviewContextMenu");
  const state = {
    job: null,
    words: [],
    spans: [],
    active: new Set(),
    controller: null,
    timer: null,
    version: 0,
    frame: null,
    holdUntil: 0,
    maxDuration: 0,
    context: null,
    replayEnd: null,
  };
  function mirrorScroll() {
    layer.scrollTop = editor.scrollTop;
    layer.scrollLeft = editor.scrollLeft;
  }
  function draw(words) {
    const text = editor.value,
      fragment = document.createDocumentFragment();
    let cursor = 0;
    state.words = [];
    state.spans = [];
    state.active = new Set();
    state.maxDuration = 0;
    for (const word of words) {
      if (
        word.char_start < cursor ||
        word.char_end > text.length ||
        word.char_end <= word.char_start ||
        !Number.isFinite(word.start) ||
        !Number.isFinite(word.end) ||
        word.end <= word.start
      )
        continue;
      fragment.append(
        document.createTextNode(text.slice(cursor, word.char_start)),
      );
      const span = document.createElement("span");
      span.className = "review-timed-word";
      span.textContent = text.slice(word.char_start, word.char_end);
      span.dataset.start = word.start;
      span.dataset.end = word.end;
      fragment.append(span);
      state.words.push(word);
      state.spans.push(span);
      state.maxDuration = Math.max(state.maxDuration, word.end - word.start);
      cursor = word.char_end;
    }
    fragment.append(document.createTextNode(text.slice(cursor) + "\n"));
    layer.replaceChildren(fragment);
    editor.classList.add("review-overlay-enabled");
    mirrorScroll();
    tick();
  }
  async function mapText() {
    if (!state.job) return;
    state.controller?.abort();
    const controller = new AbortController();
    state.controller = controller;
    const text = editor.value,
      id = state.job,
      version = ++state.version;
    try {
      const response = await fetch(
        `/api/jobs/${encodeURIComponent(id)}/correction/alignment`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
          signal: controller.signal,
        },
      );
      if (!response.ok) throw Error("Timing unavailable");
      const result = await response.json();
      if (
        version !== state.version ||
        id !== state.job ||
        text !== editor.value
      )
        return;
      draw(result.words || []);
      status.textContent = result.matched_words
        ? `${result.matched_words} of ${result.total_words} words linked to audio`
        : "Text is editable; no original word timing is available.";
    } catch (error) {
      if (error.name === "AbortError") return;
      if (version === state.version)
        status.textContent =
          "Audio timing unavailable. Your edits can still be saved and exported.";
    }
  }
  function tick() {
    const media = corrections.media;
    if (!media) return;
    if (
      state.replayEnd !== null &&
      !media.paused &&
      media.currentTime >= state.replayEnd
    ) {
      media.pause();
      state.replayEnd = null;
    }
    const time = media.currentTime,
      current = new Set();
    let newest = null;
    // Source order follows text; binary search by time is unsafe after an analyst reorders text.
    for (let i = 0; i < state.words.length; i++)
      if (state.words[i].start <= time && time < state.words[i].end)
        current.add(i);
    for (const index of state.active)
      if (!current.has(index))
        state.spans[index]?.classList.remove("review-current-word");
    for (const index of current)
      if (!state.active.has(index)) {
        state.spans[index].classList.add("review-current-word");
        newest = state.spans[index];
      }
    state.active = current;
    if (
      newest &&
      document.querySelector("#reviewFollow").checked &&
      performance.now() > state.holdUntil &&
      !document.querySelector("#correctionsTab").hidden
    ) {
      const box = editor.getBoundingClientRect(),
        word = newest.getBoundingClientRect();
      if (word.top < box.top + 20 || word.bottom > box.bottom - 25) {
        editor.scrollTop += word.top - box.top - box.height * 0.4;
        mirrorScroll();
      }
    }
  }
  function animate() {
    tick();
    if (
      corrections.media &&
      !corrections.media.paused &&
      !corrections.media.ended
    )
      state.frame = requestAnimationFrame(animate);
  }
  function atCaret() {
    const position = editor.selectionStart;
    return (
      state.words.find(
        (w) => w.char_start <= position && position < w.char_end,
      ) ||
      state.words.find((w) => w.char_start < position && position <= w.char_end)
    );
  }
  function seek(word, play = false, replay = false) {
    if (!word || !corrections.media) return;
    if (corrections.media.readyState < 1) {
      status.textContent = "The recording is still loading.";
      return;
    }
    corrections.media.currentTime = replay
      ? Math.max(0, word.start - 1.5)
      : word.start;
    state.replayEnd = replay
      ? Math.min(corrections.media.duration, word.end + 2)
      : null;
    tick();
    if (play)
      corrections.media.play().catch(() => {
        status.textContent = "Press Play on the recording to begin.";
      });
  }
  function closeMenu() {
    menu.hidden = true;
  }
  function openMenu(event) {
    event.preventDefault();
    const selected = editor.value.slice(
        editor.selectionStart,
        editor.selectionEnd,
      ),
      word = atCaret();
    state.context = {
      word,
      selected,
      start: editor.selectionStart,
      end: editor.selectionEnd,
    };
    document.querySelector("#reviewContextPlay").disabled = !word;
    document.querySelector("#reviewContextReplay").disabled = !word;
    document.querySelector("#reviewContextCopy").disabled = !selected;
    document.querySelector("#reviewContextFind").disabled = !selected;
    document.querySelector("#reviewContextNote").textContent = word
      ? "Word timing comes from the original transcript."
      : "This edited word has no original timing.";
    menu.hidden = false;
    const box = editor.getBoundingClientRect();
    menu.style.left =
      Math.max(
        8,
        Math.min(
          event.clientX || box.left + 20,
          window.innerWidth - menu.offsetWidth - 8,
        ),
      ) + "px";
    menu.style.top =
      Math.max(
        8,
        Math.min(
          event.clientY || box.top + 30,
          window.innerHeight - menu.offsetHeight - 8,
        ),
      ) + "px";
    menu.querySelector("button:not(:disabled)")?.focus();
  }
  editor.addEventListener("scroll", mirrorScroll);
  editor.addEventListener(
    "wheel",
    () => {
      state.holdUntil = performance.now() + 4000;
    },
    { passive: true },
  );
  editor.addEventListener(
    "touchstart",
    () => {
      state.holdUntil = performance.now() + 4000;
    },
    { passive: true },
  );
  editor.addEventListener("mouseup", (event) => {
    if (event.button === 0 && editor.selectionStart === editor.selectionEnd)
      seek(atCaret());
  });
  editor.addEventListener("contextmenu", openMenu);
  editor.addEventListener("keydown", (event) => {
    if (event.key === "F10" && event.shiftKey) openMenu(event);
  });
  document.addEventListener("pointerdown", (event) => {
    if (!menu.contains(event.target)) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (menu.hidden) return;
    if (event.key === "Escape") {
      closeMenu();
      editor.focus();
    }
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      const items = [...menu.querySelectorAll("button:not(:disabled)")],
        index = items.indexOf(document.activeElement);
      items[
        (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) %
          items.length
      ]?.focus();
    }
  });
  document.querySelector("#reviewContextPlay").addEventListener("click", () => {
    seek(state.context?.word, true);
    closeMenu();
  });
  document
    .querySelector("#reviewContextReplay")
    .addEventListener("click", () => {
      seek(state.context?.word, true, true);
      closeMenu();
    });
  document
    .querySelector("#reviewContextCopy")
    .addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(state.context?.selected || "");
      } catch {
        status.textContent = "Use Ctrl / ⌘ C to copy the selection.";
      }
      closeMenu();
    });
  document.querySelector("#reviewContextFind").addEventListener("click", () => {
    document.querySelector("#reviewFindPanel").hidden = false;
    document.querySelector("#reviewFindText").value =
      state.context?.selected || "";
    document.querySelector("#reviewFindText").focus();
    closeMenu();
  });
  document.querySelector("#reviewContextTime").addEventListener("click", () => {
    editor.setSelectionRange(
      state.context?.start || 0,
      state.context?.end || 0,
    );
    document.querySelector("#insertReviewTime").click();
    closeMenu();
  });
  window.ReviewSync = {
    load(id) {
      state.job = id;
      state.controller?.abort();
      clearTimeout(state.timer);
      if (state.frame) cancelAnimationFrame(state.frame);
      draw([]);
      const media = corrections.media;
      if (media) {
        for (const event of [
          "timeupdate",
          "seeking",
          "seeked",
          "loadedmetadata",
          "pause",
          "ended",
        ])
          media.addEventListener(event, tick);
        media.addEventListener("play", () => {
          if (state.frame) cancelAnimationFrame(state.frame);
          animate();
        });
      }
      mapText();
    },
    changed() {
      state.holdUntil = performance.now() + 5000;
      state.version++;
      state.controller?.abort();
      draw([]);
      status.textContent = "Updating audio links…";
      clearTimeout(state.timer);
      state.timer = setTimeout(mapText, 600);
    },
  };
  if (corrections.jobId && !editor.disabled && corrections.media)
    window.ReviewSync.load(corrections.jobId);
})();
