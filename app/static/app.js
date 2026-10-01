const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const mmss = (s) =>
  `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function humanSize(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

/** Folder ids look like 2026-09-02_1806[_slug] — show them the way a person reads a date. */
function humanWhen(id) {
  const m = /^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})/.exec(id || "");
  if (!m) return id;
  const [, y, mo, d, hh, mm] = m;
  const year = Number(y) === new Date().getFullYear() ? "" : ` ${y}`;
  return `${MONTHS[Number(mo) - 1]} ${Number(d)}${year}, ${hh}:${mm}`;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

const postJSON = (path, data) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

function svg(paths, size = 15) {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("width", size); s.setAttribute("height", size);
  s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  for (const d of paths) {
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d);
    s.append(p);
  }
  return s;
}

const ICON = {
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  spinner: ["M12 3a9 9 0 1 0 9 9"],
  mic: ["M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z", "M5 11a7 7 0 0 0 14 0"],
  speaker: ["M4 9v6h4l5 4V5L8 9H4z", "M17.5 8.5a5 5 0 0 1 0 7"],
  trash: ["M4 7h16", "M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2", "M6 7l1 13h10l1-13"],
  text: ["M6 7h12", "M6 12h12", "M6 17h7"],
  disk: ["M5 4h11l3 3v13H5z", "M9 4v5h6V4", "M8 20v-6h8v6"],
  spark: ["M12 4l1.7 4.6L18 10l-4.3 1.4L12 16l-1.7-4.6L6 10l4.3-1.4z", "M18 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"],
  doc: ["M7 3h7l4 4v14H7z", "M14 3v4h4", "M10 12h6", "M10 16h6"],
  pencil: ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
  download: ["M12 4v11", "M7.5 10.5L12 15l4.5-4.5", "M5 20h14"],
  people: ["M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z", "M2.5 20a6.5 6.5 0 0 1 13 0",
           "M16 4.3a3.5 3.5 0 0 1 0 6.4", "M18 14.2a6.5 6.5 0 0 1 3.5 5.8"],
  play: ["M8.5 5.5v13l10-6.5z"],
  pause: ["M9 5.5v13", "M15 5.5v13"],
};

// --- pipeline -------------------------------------------------------------

// Backend stages, in the order they actually happen. "finishing" is the brief
// moment between the last buffer and the drained queue; it belongs to Recording.
const BUSY = ["finishing", "transcribing", "saving", "summarizing"];

const STEPS = [
  { key: "recording",    name: "Recording",   icon: ICON.mic },
  { key: "transcribing", name: "Transcribing", icon: ICON.text },
  { key: "saving",       name: "Saving audio", icon: ICON.disk },
  { key: "summarizing",  name: "Summarizing",  icon: ICON.spark },
  { key: "ready",        name: "Minutes ready", icon: ICON.doc },
];

/** Which step index the given backend stage is sitting on. */
function stepIndex(stage) {
  if (stage === "recording" || stage === "finishing") return 0;
  if (stage === "transcribing") return 1;
  if (stage === "saving") return 2;
  if (stage === "summarizing") return 3;
  return -1; // idle
}

let finished = false;   // the last run got all the way to minutes

function renderPipeline(s) {
  const at = stepIndex(s.stage);
  const recording = s.stage === "recording" || s.stage === "finishing";
  const list = $("pipeline");
  list.innerHTML = "";

  STEPS.forEach((step, i) => {
    let state;
    if (at === -1) state = finished ? "done" : "todo";
    else if (i < at) state = "done";
    else if (i === at) state = recording ? "rec" : "now";
    else state = "todo";

    const li = el("li", `step ${state}`);
    const pip = el("span", "pip");
    const mark = svg(state === "done" ? ICON.check
                   : state === "now" ? ICON.spinner
                   : step.icon, state === "done" || state === "now" ? 16 : 17);
    if (state === "now") mark.classList.add("spin");
    pip.append(mark);

    li.append(pip, el("span", "name", step.name), el("span", "val", stepValue(step.key, s, state)));
    list.append(li);
  });

  $("pipeline-sub").textContent =
    at === -1
      ? (finished ? "Finished — minutes are in the list below" : "Nothing running")
      : recording
        ? "Transcribing as it records, so stopping is quick"
        : "Working through the tail — the transcript is already on disk";
}

function stepValue(key, s, state) {
  if (key === "recording" && (state === "rec" || state === "done") && s.elapsed_sec)
    return mmss(s.elapsed_sec);
  if (key === "transcribing" && s.closed) return `${s.transcribed}/${s.closed} chunks`;
  if (key === "summarizing" && state === "now")
    return s.batches > 1 ? `batch ${s.batch}/${s.batches}` : (s.models.llm || "");
  if (key === "ready" && state === "done") return "saved";
  if (state === "now" && s.stage_sec) return mmss(s.stage_sec);
  return "";
}

// --- status ---------------------------------------------------------------

let lastStage = null;

async function refreshStatus() {
  let s;
  try {
    s = await api("/api/status");
  } catch {
    return;
  }

  const recording = s.stage === "recording";
  const busy = BUSY.includes(s.stage);
  document.body.classList.toggle("is-recording", recording);

  $("history-section").hidden = !$("viewer").hidden;

  $("record").disabled = busy;
  $("record").className = `orb ${recording ? "orb-live" : "orb-idle"}`;
  $("record").title = recording ? "Stop recording" : "Start recording";
  $("settings-open").disabled = recording || busy;
  $("quit").disabled = recording || busy;

  const DECK = {
    recording: ["Recording", "Both sides are captured separately, so the minutes can tell you apart from everyone else."],
    finishing: ["Closing the recording", "Waiting for the last chunk of audio to be written."],
    transcribing: ["Transcribing", "Working through the chunks that were still in the queue when you stopped."],
    saving: ["Saving audio", "Mixing both channels into one file — you on the left, them on the right."],
    summarizing: ["Writing the minutes", s.batches > 1
      ? `Long meeting — summarizing it in ${s.batches} batches so nothing is left out. Batch ${s.batch} of ${s.batches}.`
      : `${s.models.llm || "The model"} is reading the whole transcript. This is the slow part on a CPU.`],
  };

  $("deck-title").className = `deck-title${recording ? " timer" : ""}`;
  if (recording) {
    $("deck-title").textContent = mmss(s.elapsed_sec);
    $("deck-note").textContent = DECK.recording[1];
  } else if (busy) {
    $("deck-title").textContent = DECK[s.stage][0];
    $("deck-note").textContent = DECK[s.stage][1];
  } else {
    $("deck-title").textContent = "Ready to record";
    $("deck-note").textContent = DECK.recording[1];
  }

  const showBar = (recording || busy) && s.closed > 0;
  $("deck-progress").hidden = !showBar;
  if (showBar) {
    $("done").textContent = s.transcribed;
    $("total").textContent = s.closed;
    $("bar").style.width = `${Math.round((s.transcribed / s.closed) * 100)}%`;
  }

  const devs = $("deck-devices");
  devs.innerHTML = "";
  if (!busy) {
    for (const [key, icon] of [["mic", ICON.mic], ["loopback", ICON.speaker]]) {
      const name = (s.devices || {})[key];
      if (!name) continue;
      const item = el("span");
      item.append(svg(icon, 15), el("span", null, name.replace(/\s*\(.*\)\s*/, " ").trim()));
      devs.append(item);
    }
  }

  renderPipeline(s);

  const badge = $("badge");
  badge.className = `badge ${recording ? "badge-rec" : busy ? "badge-busy" : "badge-safe"}`;
  $("badge-text").textContent = recording ? "Recording" : busy ? "Working" : "Stays on this machine";
  $("subtitle").textContent = s.models && s.models.whisper
    ? `${s.models.whisper} · ${s.models.llm}`
    : "Records your mic and everything you hear";

  showWarn((s.errors || []).join(" · "));

  // A finished run lands back on idle: refresh the list once.
  if (lastStage && lastStage !== "idle" && s.stage === "idle") refreshHistory();
  lastStage = s.stage;
  return s;
}

function showWarn(text) {
  $("warn").hidden = !text;
  $("warn").textContent = text || "";
}

// --- record button --------------------------------------------------------

$("record").onclick = async () => {
  const recording = document.body.classList.contains("is-recording");
  $("record").disabled = true;
  showWarn("");
  try {
    if (recording) {
      const r = await postJSON("/api/stop", {});
      finished = true;
      await refreshHistory();
      if (r.errors.length) showWarn(r.errors.join(" · "));
      if (r.meeting_id) openMeeting(r.meeting_id);
    } else {
      finished = false;
      await postJSON("/api/start", {});
    }
  } catch (e) {
    showWarn(e.message);
  }
  $("record").disabled = false;
  refreshStatus();
};

// --- history --------------------------------------------------------------

async function refreshHistory() {
  let items;
  try {
    items = await api("/api/meetings");
  } catch {
    return;
  }

  const list = $("history");
  list.innerHTML = "";

  const total = items.reduce((a, m) => a + (m.size_bytes || 0), 0);
  $("history-summary").textContent = items.length
    ? `${items.length} on disk · ${humanSize(total)}`
    : "";

  if (!items.length) {
    list.append(el("div", "empty", "No meetings yet. Press record when your next one starts."));
    return;
  }

  for (const m of items) {
    const row = el("div", "row");

    const open = el("button", "open");
    open.append(el("span", "when", humanWhen(m.id)), el("span", "id", m.id));
    open.onclick = () => openMeeting(m.id);

    const tag = m.has_minutes
      ? el("span", "tag tag-min", "Minutes")
      : m.has_transcript
        ? el("span", "tag tag-none", "No minutes")
        : el("span", "tag tag-raw", "Audio only");

    const del = el("button", "icon-btn trash");
    del.title = "Delete meeting";
    del.append(svg(ICON.trash, 15));
    del.onclick = async () => {
      const size = m.size_bytes ? ` (${humanSize(m.size_bytes)})` : "";
      if (!confirm(`Delete ${m.id}?${size}\n\nThis removes the folder, its audio and its minutes.`)) return;
      try {
        await api(`/api/meetings/${m.id}`, { method: "DELETE" });
        if (viewing === m.id) closeViewer();
        refreshHistory();
      } catch (e) {
        showWarn(e.message);
      }
    };

    row.append(open, tag,
               el("span", "dur", m.duration_sec ? mmss(m.duration_sec) : "—"),
               el("span", "size", humanSize(m.size_bytes)),
               del);
    list.append(row);
  }
}

// --- viewer ---------------------------------------------------------------

let viewing = null;
let loaded = { minutes: "", transcript: "", meta: null };

async function openMeeting(id) {
  if (!leaveEditing()) return;
  let m;
  try {
    m = await api(`/api/meetings/${id}`);
  } catch (e) {
    return showWarn(e.message);
  }

  viewing = id;
  loaded = { minutes: m.minutes || "", transcript: m.transcript || "", meta: m };
  // A plain link: the browser does the download, and the server builds the
  // .docx from the saved minutes.md, so save any edits first.
  $("minutes-export").href = `/api/meetings/${id}/minutes.docx`;

  $("viewer-title").textContent = humanWhen(id);
  $("viewer-meta").textContent = [
    id,
    m.duration_sec ? mmss(m.duration_sec) : null,
    m.whisper_model,
    m.llm_model,
    m.minutes_edited_at ? "minutes edited" : null,
  ].filter(Boolean).join(" · ");

  const files = $("viewer-files");
  files.innerHTML = "";
  if (m.minutes) files.append(el("span", "chip", "minutes.md"));
  if (m.transcript) files.append(el("span", "chip", "transcript.txt"));
  if (m.audio) files.append(el("span", "chip", m.audio));
  files.append(el("span", "path", `meetings/${id}/`));

  const failure = (m.capture_errors || []).join(" · ");
  $("viewer-banner").hidden = !!m.minutes || !failure;
  $("viewer-banner").className = "banner soft";
  $("viewer-banner").textContent = failure ? `No minutes for this meeting. ${failure}` : "";

  $("viewer").hidden = false;
  $("history-section").hidden = true;
  renderSpeakerState();
  watchMeeting();
  showTab(m.minutes ? "minutes" : "transcript");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

let currentTab = "minutes";

function showTab(which) {
  if (!leaveEditing()) return;
  currentTab = which;
  $("tab-minutes").className = `seg${which === "minutes" ? " on" : ""}`;
  $("tab-transcript").className = `seg${which === "transcript" ? " on" : ""}`;
  $("minutes-edit").hidden = which !== "minutes";
  $("minutes-export").hidden = which !== "minutes" || !loaded.minutes;
  $("minutes-edit-label").textContent = loaded.minutes ? "Edit" : "Write minutes";

  const body = $("viewer-body");
  body.innerHTML = "";
  body.className = `glass viewer-body${which === "transcript" ? " transcript" : ""}`;
  body.classList.toggle("has-actions", !$("speakers-open").hidden);

  const text = loaded[which];
  if (!text) {
    body.append(el("div", "empty",
      which === "minutes" ? "No minutes were written for this meeting." : "No transcript."));
    return;
  }
  (which === "minutes" ? renderMinutes : renderTranscript)(body, text);
}

/** minutes.md is the four-section format summarize.py asks for — render it, don't dump it. */
function renderMinutes(body, md) {
  let accent = "";
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    if (!line) continue;

    if (line.startsWith("> ")) {
      body.append(el("div", "md-quote", line.slice(2).replace(/\*\*/g, "")));
    } else if (line.startsWith("#")) {
      const title = line.replace(/^#+\s*/, "");
      if (body.children.length) body.append(el("div", "md-rule"));
      body.append(el("div", "md-h", title));
      const low = title.toLowerCase();
      // Not "warn": that class is the red error banner.
      accent = low.includes("decision") ? "good" : low.includes("question") ? "open" : "";
    } else if (/^[-*]\s+/.test(line)) {
      body.append(el("div", `md-li ${accent}`.trim(), line.replace(/^[-*]\s+/, "").replace(/\*\*/g, "")));
    } else {
      body.append(el("p", "md-p", line.replace(/\*\*/g, "")));
    }
  }
}

/** transcript.txt lines look like: [00:01:12] Maple: text (You, Them or a name) */
function renderTranscript(body, text) {
  for (const raw of text.split("\n")) {
    const m = /^\[(\d\d:\d\d:\d\d)\]\s+([^:]+?):\s*(.*)$/.exec(raw);
    if (!m) {
      if (raw.trim()) body.append(el("div", "line", raw));
      continue;
    }
    const [, at, who, said] = m;
    const line = el("div", "line");
    line.append(
      el("span", "at", at),
      el("span", `who ${voiceClass(who)}`, who),
      el("span", "said", said),
    );
    body.append(line);
  }
}

function closeViewer() {
  if (!leaveEditing()) return;
  viewing = null;
  watchMeeting();
  $("viewer").hidden = true;
  $("history-section").hidden = false;
}

$("tab-minutes").onclick = () => showTab("minutes");
$("tab-transcript").onclick = () => showTab("transcript");
$("viewer-close").onclick = closeViewer;

// --- editing minutes --------------------------------------------------------

// The minutes are a draft until a person has checked them, so they are edited
// as the raw Markdown in minutes.md: what you type is exactly what is saved.
let editing = false;

$("minutes-edit").append(svg(ICON.pencil, 15), el("span", null, "Edit"));
$("minutes-edit").lastChild.id = "minutes-edit-label";
$("minutes-export").append(svg(ICON.download, 15), el("span", null, "Export"));

function setEditing(on) {
  editing = on;
  $("speakers-open").hidden = on || !speakersApply();
  $("minutes-edit").hidden = on;
  $("minutes-export").hidden = on || !loaded.minutes;  // would export the unsaved version
  $("minutes-cancel").hidden = !on;
  $("minutes-save").hidden = !on;
}

/** True if it is fine to leave the editor: nothing changed, or the user agreed to lose it. */
function leaveEditing() {
  if (!editing) return true;
  if ($("minutes-text").value !== loaded.minutes
      && !confirm("Discard your changes to the minutes?")) return false;
  setEditing(false);
  return true;
}

$("minutes-edit").onclick = () => {
  const body = $("viewer-body");
  body.innerHTML = "";
  body.className = "glass viewer-body editing";
  const box = el("textarea", "minutes-editor");
  box.id = "minutes-text";
  box.value = loaded.minutes;
  box.setAttribute("aria-label", "Meeting minutes");
  body.append(box);
  // Grow with the text instead of scrolling inside a box inside the page.
  const fit = () => { box.style.height = "auto"; box.style.height = `${box.scrollHeight}px`; };
  box.oninput = fit;
  fit();
  setEditing(true);
  box.focus({ preventScroll: true });
  box.setSelectionRange(0, 0);
};

$("minutes-cancel").onclick = () => showTab("minutes");

$("minutes-save").onclick = async () => {
  const text = $("minutes-text").value;
  try {
    await api(`/api/meetings/${viewing}/minutes`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes: text }),
    });
  } catch (e) {
    return showWarn(e.message);  // stay in the editor; nothing is lost
  }
  loaded.minutes = text;
  setEditing(false);
  if (!$("viewer-meta").textContent.includes("minutes edited")) {
    $("viewer-meta").textContent += " · minutes edited";
  }
  $("viewer-banner").hidden = true;  // any "no minutes" notice is now out of date
  showTab("minutes");
  refreshHistory();
};

// --- settings -------------------------------------------------------------

function fill(select, options, current) {
  select.innerHTML = "";
  for (const o of options) {
    const opt = el("option", null, o.label);
    opt.value = o.value;
    if (String(o.value) === String(current)) opt.selected = true;
    select.append(opt);
  }
}

$("settings-open").onclick = async () => {
  let d;
  try {
    d = await api("/api/settings");
  } catch (e) {
    return showWarn(e.message);
  }
  const s = d.settings;

  // Only what is on disk, like the Ollama list. The current choice stays even
  // if missing, so saving never silently switches it.
  const whispers = d.whisper_models.filter(
    (m) => (d.whisper_downloaded || []).includes(m) || m === s.whisper_model);
  fill($("s-whisper"), whispers.map((m) => ({ value: m, label: m })), s.whisper_model);

  const llms = d.llm_models.length ? d.llm_models : [s.llm_model];
  fill($("s-llm"), llms.map((m) => ({ value: m, label: m })), s.llm_model);
  $("llm-hint").textContent = d.llm_models.length
    ? "Bigger models write more accurate minutes but are slower and use more memory."
    : "Ollama is not reachable, so the installed models could not be listed.";

  const devices = (list) => [{ value: "", label: "System default" }]
    .concat(list.map((x) => ({ value: x.index, label: x.name })));
  fill($("s-mic"), devices(d.devices.mics || []), s.mic_index ?? "");
  fill($("s-loop"), devices(d.devices.loopbacks || []), s.loopback_index ?? "");

  $("s-dir").value = s.meetings_dir;

  const v = d.version || {};
  $("u-version").textContent = v.commit ? `Version ${v.commit} · ${v.date}` : "Version unknown";
  $("u-result").hidden = true;
  $("u-apply").hidden = true;
  $("u-check").disabled = false;
  $("s-msg").textContent = d.devices.error || "";
  $("settings").showModal();
};

$("s-cancel").onclick = (e) => { e.preventDefault(); $("settings").close(); };

$("s-save").onclick = async (e) => {
  e.preventDefault();
  const num = (v) => (v === "" ? null : Number(v));
  try {
    await postJSON("/api/settings", {
      whisper_model: $("s-whisper").value,
      llm_model: $("s-llm").value,
      mic_index: num($("s-mic").value),
      loopback_index: num($("s-loop").value),
      meetings_dir: $("s-dir").value,
    });
    $("settings").close();
    showWarn("");
    refreshHistory();
    refreshStatus();
  } catch (err) {
    $("s-msg").textContent = err.message;
  }
};

// --- speakers ---------------------------------------------------------------

// pyannote tells the Them voices apart after the minutes are written; it runs
// in the background and takes about as long as the meeting did.
const IDENTIFYING = ["queued", "running"];

/** Colour for a transcript label: You blue, each voice its panel colour. */
function voiceClass(name) {
  const rows = (loaded.meta && loaded.meta.speakers) || [];
  const you = rows.find((r) => r.label === "You");
  if (name === (you ? you.name : "You")) return "you";
  const i = rows.filter((r) => r.label !== "You").findIndex((r) => r.name === name);
  return i >= 0 ? `s${i % 6}` : "them";
}

/** The Speakers button makes sense once there is a Them side to split. */
function speakersApply() {
  const m = loaded.meta || {};
  return Boolean(m.speakers_state) || /\] Them:/.test(loaded.transcript);
}

function renderSpeakerState() {
  const m = loaded.meta || {};
  const b = $("speakers-open");
  const busy = IDENTIFYING.includes(m.speakers_state);
  b.hidden = editing || !speakersApply();
  b.disabled = busy;
  b.title = busy
    ? "Working out who said what, in the background. The minutes are already done; this takes about as long as the meeting."
    : m.speakers_state === "failed" ? `Last try failed: ${m.speakers_error || "unknown error"}` : "";
  const icon = svg(busy ? ICON.spinner : ICON.people, 15);
  if (busy) icon.classList.add("spin");
  const label = busy ? "Identifying speakers…"
    : m.speakers_state === "done" ? "Speakers" : "Identify speakers";
  b.replaceChildren(icon, el("span", null, label));

  const updating = m.minutes_state === "updating";
  $("minutes-edit").disabled = updating;
  const status = $("viewer-status");
  status.hidden = !(updating || m.minutes_state === "failed");
  status.className = `banner ${updating ? "info" : "soft"}`;
  status.textContent = updating
    ? "Updating the minutes with the speakers' names. You can keep using the app meanwhile."
    : m.minutes_state === "failed" ? `Could not update the minutes: ${m.minutes_error || "unknown error"}` : "";
}

// While something runs in the background for the open meeting, check on it.
let pollTimer = null;

function watchMeeting() {
  const m = loaded.meta || {};
  const busy = viewing && (IDENTIFYING.includes(m.speakers_state) || m.minutes_state === "updating");
  if (busy && !pollTimer) pollTimer = setInterval(pollMeeting, 4000);
  if (!busy && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function pollMeeting() {
  const id = viewing;
  let m;
  try {
    m = await api(`/api/meetings/${id}`);
  } catch {
    return;
  }
  if (id !== viewing) return;
  const before = loaded.meta || {};
  loaded.meta = m;
  if (!editing) {
    loaded.transcript = m.transcript || "";
    loaded.minutes = m.minutes || "";
    if (before.speakers_state !== m.speakers_state || before.minutes_state !== m.minutes_state) {
      showTab(currentTab);
    }
  }
  renderSpeakerState();
  watchMeeting();
}

$("speakers-open").onclick = async () => {
  if (loaded.meta.speakers_state === "done") return openSpeakers();
  try {
    loaded.meta = await postJSON(`/api/meetings/${viewing}/speakers/identify`, {});
  } catch (e) {
    return showWarn(e.message);
  }
  renderSpeakerState();
  watchMeeting();
};

let playing = null;  // the one sample playing: { audio, button }

function stopPlaying() {
  if (!playing) return;
  playing.audio.pause();
  playing.button.classList.remove("on");
  playing.button.replaceChildren(svg(ICON.play, 16));
  playing = null;
}

function openSpeakers() {
  const list = $("speakers-list");
  list.innerHTML = "";
  let n = 0;
  for (const r of loaded.meta.speakers) {
    const colour = r.label === "You" ? "you" : `s${n++ % 6}`;
    const row = el("div", `speaker-row ${colour}`);

    const play = el("button", "play");
    play.type = "button";
    play.setAttribute("aria-label", `Play a sample of ${r.label}`);
    play.append(svg(ICON.play, 16));
    play.onclick = () => {
      const again = playing && playing.button === play;
      stopPlaying();
      if (again) return;
      const audio = new Audio(`/api/meetings/${viewing}/speakers/${encodeURIComponent(r.label)}/sample`);
      audio.onended = stopPlaying;
      audio.play().catch(() => { $("speakers-msg").textContent = "Could not play that sample."; stopPlaying(); });
      playing = { audio, button: play };
      play.classList.add("on");
      play.replaceChildren(svg(ICON.pause, 16));
    };

    const input = el("input");
    input.type = "text";
    input.maxLength = 60;
    input.placeholder = r.label;
    input.value = r.name === r.label ? "" : r.name;
    input.dataset.label = r.label;
    input.setAttribute("aria-label", `Name for ${r.label}`);

    const lines = `${r.lines} line${r.lines === 1 ? "" : "s"}`;
    const text = el("div", "who-text");
    text.append(input, el("span", "detail", `${r.label} · ${lines} · ${mmss(r.seconds)}`));
    row.append(play, text);
    list.append(row);
  }
  $("speakers-msg").textContent = "Play each voice, then type who it is.";
  $("speakers").showModal();
}

$("speakers").onclose = stopPlaying;

async function saveSpeakers(update) {
  const names = {};
  for (const input of $("speakers-list").querySelectorAll("input")) {
    names[input.dataset.label] = input.value.trim();
  }
  if (update && loaded.meta.minutes_edited_at
      && !confirm("Updating the minutes replaces the edits you made to them. Continue?")) return;
  let m;
  try {
    m = await api(`/api/meetings/${viewing}/speakers`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names }),
    });
    if (update) m = await postJSON(`/api/meetings/${viewing}/minutes/update`, {});
  } catch (e) {
    $("speakers-msg").textContent = e.message;
    return;
  }
  $("speakers").close();
  loaded.meta = m;
  loaded.transcript = m.transcript || "";
  loaded.minutes = m.minutes || "";
  renderSpeakerState();
  showTab(currentTab);
  watchMeeting();
}

$("speakers-save").onclick = () => saveSpeakers(false);
$("speakers-save-update").onclick = () => saveSpeakers(true);

// --- updates ----------------------------------------------------------------

// Settings -> Updates. Checking asks GitHub what is new; updating closes the
// app, updates it in its own window and starts it again, and this page waits
// for the new version and reloads itself.

$("u-check").onclick = async () => {
  const out = $("u-result");
  $("u-check").disabled = true;
  $("u-apply").hidden = true;
  out.hidden = false;
  out.textContent = "Checking…";
  let r;
  try {
    r = await api("/api/updates");
  } catch (e) {
    out.textContent = e.message;
    $("u-check").disabled = false;
    return;
  }
  $("u-check").disabled = false;
  if (!r.changes.length) {
    out.textContent = "You're up to date.";
    return;
  }
  out.textContent = `${r.changes.length} update${r.changes.length === 1 ? "" : "s"} available:`;
  const list = el("ul", "update-list");
  for (const c of r.changes.slice(0, 8)) list.append(el("li", null, c.subject));
  if (r.changes.length > 8) list.append(el("li", null, `and ${r.changes.length - 8} more`));
  out.append(list);
  if (r.local_edits) {
    out.append(el("p", null, "This copy has edited files, so it can't update itself."));
    return;
  }
  $("u-apply").hidden = false;
};

$("u-apply").onclick = async () => {
  $("u-apply").disabled = true;
  try {
    await postJSON("/api/updates/apply", {});
  } catch (e) {
    $("u-result").textContent = e.message;
    $("u-apply").disabled = false;
    return;
  }
  $("settings").close();
  waitForNewVersion();
};

/** Wait for the app to go down and come back, then reload into the new version. */
async function waitForNewVersion() {
  const banner = $("updating");
  banner.hidden = false;
  banner.replaceChildren(svg(ICON.spinner, 16), el("span", null,
    "Updating MeetingAI. A window shows the progress; this page reloads by itself when it's done."));
  banner.firstChild.classList.add("spin");
  const started = Date.now();
  let wentDown = false;
  for (;;) {
    await new Promise((ok) => setTimeout(ok, 2000));
    let up = false;
    try {
      up = (await fetch("/api/status", { cache: "no-store" })).ok;
    } catch {
      up = false;
    }
    if (!up) wentDown = true;
    if (up && wentDown) return location.reload();
    if (Date.now() - started > 20 * 60 * 1000) {
      banner.replaceChildren(el("span", null,
        "The update is taking longer than expected. Check the Updating MeetingAI window."));
      return;
    }
  }
}

// --- quit -------------------------------------------------------------------

$("quit").onclick = async () => {
  if (!confirm("Quit MeetingAI?")) return;
  const quit = (force) => postJSON("/api/quit", { force });
  try {
    await quit(false);
  } catch (e) {
    if (e.message !== "busy") return showWarn(e.message);
    if (!confirm("Speakers or minutes are still being worked out. Quit anyway? "
                 + "Speaker identification carries on the next time you start MeetingAI.")) return;
    try {
      await quit(true);
    } catch (e2) {
      return showWarn(e2.message);
    }
  }
  closedScreen();
};

/** What the tab shows once the app has stopped: nothing on it works any more. */
function closedScreen() {
  clearInterval(statusTimer);
  const shell = document.querySelector("main.shell");
  shell.replaceChildren();
  const note = el("section", "glass deck closed");
  note.append(el("div", "deck-title", "MeetingAI is closed"),
              el("div", "deck-note", "Start it again from the Start menu. You can close this tab."));
  shell.append(note);
}

// --- go -------------------------------------------------------------------

const statusTimer = setInterval(refreshStatus, 1000);
refreshStatus();
refreshHistory();
