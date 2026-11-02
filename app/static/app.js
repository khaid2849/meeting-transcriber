"use strict";

const STATUS = {
  queued: "Đang chờ xử lý",
  converting: "Đang chuyển định dạng",
  transcribing: "Đang chuyển thành văn bản",
  done: "Xong",
  error: "Lỗi",
};
const ACTIVE = ["queued", "converting", "transcribing"];
const RUNNING = ["converting", "transcribing"];
const SPEEDS = [1, 1.25, 1.5, 2];
const POLL_MS = 2000;

const $ = (id) => document.getElementById(id);
const audio = $("audio");

const state = {
  meetings: [],
  selected: null, // meeting id
  meeting: null, // full meeting dict of the selected one
  renderedKey: null, // "<id>:<status>" of the transcript currently on screen
  timer: null,
};

/* ---------- helpers ---------- */

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function clock(sec) {
  if (sec == null || !isFinite(sec)) return "0:00";
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

function longDuration(sec) {
  if (!sec) return "";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return [h && `${h} giờ`, m && `${m} phút`, !h && `${s % 60} giây`].filter(Boolean).join(" ");
}

function parseDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function shortDate(iso) {
  return parseDay(iso).toLocaleDateString("vi-VN", { day: "numeric", month: "short", year: "numeric" });
}

function longDate(iso) {
  return parseDay(iso).toLocaleDateString("vi-VN", { day: "numeric", month: "long", year: "numeric" });
}

function todayIso() {
  const now = new Date();
  return new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function groupLabel(iso) {
  const days = Math.round((parseDay(todayIso()) - parseDay(iso)) / 86400000);
  if (days <= 0) return "Hôm nay";
  if (days === 1) return "Hôm qua";
  if (days < 7) return "7 ngày qua";
  if (days < 30) return "30 ngày qua";
  const d = parseDay(iso);
  return `Tháng ${d.getMonth() + 1}, ${d.getFullYear()}`;
}

function statusLine(m) {
  let text = STATUS[m.status] || m.status;
  if (RUNNING.includes(m.status) && m.progress > 0) text += ` ${Math.round(m.progress * 100)}%`;
  return text;
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? null : res.json();
}

const jsonBody = (data) => ({ headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

let toastTimer = null;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1800);
}

/* ---------- polling ---------- */

function schedule() {
  clearTimeout(state.timer);
  const busy = state.meetings.some((m) => ACTIVE.includes(m.status));
  if (busy) state.timer = setTimeout(refresh, POLL_MS);
}

async function refresh() {
  try {
    state.meetings = await api("/api/meetings");
  } catch (err) {
    $("list").replaceChildren(el("div", { className: "list-empty", textContent: err.message }));
    state.timer = setTimeout(refresh, 5000);
    return;
  }
  renderList();
  const current = state.meetings.find((m) => m.id === state.selected);
  if (state.selected && current && (ACTIVE.includes(current.status) || current.status !== state.meeting?.status)) {
    await loadMeeting(state.selected);
  }
  schedule();
}

/* ---------- sidebar ---------- */

function renderList() {
  const list = $("list");
  if (!state.meetings.length) {
    list.replaceChildren(el("div", { className: "list-empty", textContent: "Chưa có cuộc họp nào" }));
    return;
  }
  const nodes = [];
  let lastGroup = null;
  for (const m of state.meetings) {
    const group = groupLabel(m.date);
    if (group !== lastGroup) {
      nodes.push(el("div", { className: "group", textContent: group }));
      lastGroup = group;
    }
    const right = ACTIVE.includes(m.status) || m.status === "error"
      ? el("span", { className: m.status === "error" ? "state-error" : "state-active", textContent: statusLine(m) })
      : el("span", { textContent: clock(m.duration) });
    const item = el("a", { className: "item", href: `/m/${m.id}` },
      el("div", { className: "t", textContent: m.title }),
      el("div", { className: "sub" }, el("span", { textContent: shortDate(m.date) }), right),
    );
    if (RUNNING.includes(m.status)) {
      const fill = el("div");
      fill.style.width = `${Math.round((m.progress || 0) * 100)}%`;
      item.append(el("div", { className: "bar" }, fill));
    }
    if (m.id === state.selected) item.classList.add("selected");
    item.onclick = (e) => { e.preventDefault(); select(m.id); };
    nodes.push(item);
  }
  list.replaceChildren(...nodes);
}

/* ---------- detail ---------- */

function showEmpty() {
  $("meeting").hidden = true;
  $("empty").hidden = false;
  const none = !state.meetings.length;
  $("empty-text").textContent = none ? "Chưa có cuộc họp nào. Kéo thả file ghi âm vào cửa sổ để bắt đầu." : "Chọn một cuộc họp ở bên trái";
  $("empty-new").hidden = !none;
  for (const id of ["copy", "download", "delete"]) $(id).disabled = true;
  $("retry").hidden = true;
}

async function select(id, push = true) {
  if (push && id !== state.selected) history.pushState({}, "", id ? `/m/${id}` : "/");
  state.selected = id;
  state.renderedKey = null;
  audio.pause();
  audio.removeAttribute("src");
  audio.load(); // also resets playbackRate
  $("speed").textContent = "1×";
  $("window").classList.toggle("show-detail", !!id);
  renderList();
  if (!id) {
    state.meeting = null;
    showEmpty();
    return;
  }
  await loadMeeting(id);
  $("scroll").scrollTop = 0;
}

async function loadMeeting(id) {
  let data;
  try {
    data = await api(`/api/meetings/${id}`);
  } catch (err) {
    if (id === state.selected) select(null);
    toast(err.message);
    return;
  }
  if (id !== state.selected) return;
  renderMeeting(data.meeting, data.paragraphs);
}

function renderMeeting(m, paragraphs) {
  state.meeting = m;
  $("empty").hidden = true;
  $("meeting").hidden = false;
  document.title = m.title;

  if (document.activeElement !== $("title")) $("title").value = m.title;
  $("date").value = m.date;
  $("date-btn").textContent = longDate(m.date);
  $("meta").textContent = [longDuration(m.duration), m.model && m.model.split("/").pop().replace(/-mlx$/, "")]
    .filter(Boolean).join(" · ");

  const done = m.status === "done";
  const active = ACTIVE.includes(m.status);
  $("status").hidden = done;
  $("status-text").className = `status-text${m.status === "error" ? " error" : ""}`;
  $("status-text").textContent = m.status === "error" ? `Lỗi: ${m.error}` : statusLine(m);
  $("progress").parentElement.hidden = !active;
  $("progress").style.width = `${Math.round((m.progress || 0) * 100)}%`;

  $("retry").hidden = m.status !== "error";
  $("copy").disabled = $("download").disabled = !done;
  $("delete").disabled = RUNNING.includes(m.status);
  $("player").hidden = !done;
  if (done && !audio.getAttribute("src")) {
    audio.src = `/api/meetings/${m.id}/audio`;
    updatePlayer();
  }

  const key = `${m.id}:${m.status}`;
  if (key !== state.renderedKey) {
    renderTranscript(done ? paragraphs : null, active);
    state.renderedKey = key;
  }
}

function renderTranscript(paragraphs, active) {
  const box = $("transcript");
  if (!paragraphs) {
    box.replaceChildren(active ? el("p", { className: "transcript-empty", textContent: "Bản ghi sẽ hiện ở đây khi xử lý xong. Bạn có thể đóng trang và quay lại sau." }) : "");
    return;
  }
  if (!paragraphs.length) {
    box.replaceChildren(el("p", { className: "transcript-empty", textContent: "Không nhận ra lời nói nào trong file này." }));
    return;
  }
  box.replaceChildren(...paragraphs.map((p) => {
    const ts = el("button", { className: "ts", textContent: clock(p.start), title: "Nghe từ đoạn này" });
    ts.onclick = () => { audio.currentTime = p.start; audio.play(); };
    const node = el("div", { className: "para" }, ts, el("p", { textContent: p.text }));
    node.dataset.start = p.start;
    return node;
  }));
}

/* ---------- player ---------- */

function updatePlayer() {
  const dur = audio.duration || state.meeting?.duration || 0;
  const pct = dur ? (audio.currentTime / dur) * 100 : 0;
  const scrub = $("scrub");
  if (!scrub.matches(":active")) scrub.value = Math.round(pct * 10);
  scrub.style.setProperty("--pct", `${pct}%`);
  $("t-now").textContent = clock(audio.currentTime);
  $("t-left").textContent = `-${clock(Math.max(0, dur - audio.currentTime))}`;

  let current = null;
  for (const p of $("transcript").children) {
    if (Number(p.dataset.start) <= audio.currentTime) current = p; else break;
  }
  for (const p of $("transcript").querySelectorAll(".para.current")) if (p !== current) p.classList.remove("current");
  if (current && !current.classList.contains("current") && audio.currentTime > 0) current.classList.add("current");
}

audio.ontimeupdate = updatePlayer;
audio.onloadedmetadata = updatePlayer;
audio.onplay = () => { $("play").classList.add("playing"); $("play").setAttribute("aria-label", "Tạm dừng"); };
audio.onpause = () => { $("play").classList.remove("playing"); $("play").setAttribute("aria-label", "Phát"); };
$("play").onclick = () => (audio.paused ? audio.play() : audio.pause());
$("back15").onclick = () => { audio.currentTime = Math.max(0, audio.currentTime - 15); };
$("fwd15").onclick = () => { audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + 15); };
$("scrub").oninput = (e) => {
  const dur = audio.duration || state.meeting?.duration || 0;
  if (dur) audio.currentTime = (e.target.value / 1000) * dur;
};
$("speed").onclick = () => {
  const next = SPEEDS[(SPEEDS.indexOf(audio.playbackRate) + 1) % SPEEDS.length];
  audio.playbackRate = next;
  $("speed").textContent = `${next}×`;
};
$("scroll").addEventListener("scroll", () => {
  const player = $("player");
  player.classList.toggle("stuck", !player.hidden && $("scroll").scrollTop > player.offsetTop - 1);
});
document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName) || $("sheet").open;
  if (e.code === "Space" && !typing && !$("player").hidden) {
    e.preventDefault();
    audio.paused ? audio.play() : audio.pause();
  }
});

/* ---------- toolbar actions ---------- */

async function patch(changes) {
  try {
    const m = await api(`/api/meetings/${state.selected}`, { method: "PATCH", ...jsonBody(changes) });
    state.meeting = m;
    const i = state.meetings.findIndex((x) => x.id === m.id);
    if (i >= 0) state.meetings[i] = m;
    state.meetings.sort((a, b) => (b.date + b.created_at).localeCompare(a.date + a.created_at));
    renderList();
    return true;
  } catch (err) {
    toast(`Không lưu được: ${err.message}`);
    return false;
  }
}

$("title").onkeydown = (e) => { if (e.key === "Enter") e.target.blur(); if (e.key === "Escape") { e.target.value = state.meeting.title; e.target.blur(); } };
$("title").onchange = async () => {
  if (!(await patch({ title: $("title").value }))) $("title").value = state.meeting.title;
  else document.title = state.meeting.title;
};
$("date-btn").onclick = () => {
  try { $("date").showPicker(); } catch (_) { $("date").focus(); }
};
$("date").onchange = async () => {
  if (!$("date").value || !(await patch({ date: $("date").value }))) $("date").value = state.meeting.date;
  $("date-btn").textContent = longDate(state.meeting.date);
};

$("copy").onclick = async () => {
  // Safari only allows clipboard writes started synchronously in the click, so hand it a promise.
  const blob = fetch(`/api/meetings/${state.selected}/transcript.md`).then((r) => r.blob())
    .then((b) => new Blob([b], { type: "text/plain" }));
  try {
    if (window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
    } else {
      await navigator.clipboard.writeText(await (await blob).text());
    }
    toast("Đã copy bản ghi");
  } catch (err) {
    toast("Không copy được, hãy dùng nút tải .md");
  }
};
$("download").onclick = () => { location.href = `/api/meetings/${state.selected}/transcript.md`; };
$("retry").onclick = async () => {
  try {
    await api(`/api/meetings/${state.selected}/retry`, { method: "POST" });
  } catch (err) {
    toast(err.message);
  }
  refresh();
};
$("delete").onclick = async () => {
  if (!confirm(`Xóa "${state.meeting.title}" cùng toàn bộ file audio và bản ghi?`)) return;
  try {
    await api(`/api/meetings/${state.selected}`, { method: "DELETE" });
  } catch (err) {
    toast(err.message);
    return;
  }
  await select(null);
  refresh();
};
$("back").onclick = () => select(null);
window.onpopstate = () => select(idFromPath(), false);

/* ---------- upload ---------- */

let pendingFile = null;
let xhr = null;

function setFile(file) {
  if (!file) return;
  pendingFile = file;
  $("file-name").textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
  $("file-pick").classList.add("has-file");
  if (!$("f-title").value) $("f-title").value = file.name.replace(/\.[^.]+$/, "");
  $("submit").disabled = false;
}

function openSheet(file = null) {
  if (xhr) return;
  pendingFile = null;
  $("upload-form").reset();
  $("f-date").value = todayIso();
  $("file-name").textContent = "Chọn file hoặc kéo thả vào đây";
  $("file-pick").classList.remove("has-file", "over");
  $("submit").disabled = true;
  $("upload-msg").textContent = "";
  $("upload-msg").className = "sheet-msg";
  $("upload-progress").hidden = true;
  if (!$("sheet").open) $("sheet").showModal();
  setFile(file);
}

$("new").onclick = () => openSheet();
$("empty-new").onclick = () => openSheet();
$("file").onchange = () => setFile($("file").files[0]);
$("cancel").onclick = () => {
  if (xhr) { xhr.abort(); return; }
  $("sheet").close();
};
$("sheet").addEventListener("cancel", (e) => { if (xhr) e.preventDefault(); });

$("upload-form").onsubmit = (e) => {
  e.preventDefault();
  if (!pendingFile || xhr) return;
  const form = new FormData();
  form.append("file", pendingFile);
  form.append("title", $("f-title").value);
  form.append("date", $("f-date").value);
  form.append("keywords", $("f-keywords").value);

  const bar = $("upload-progress");
  const msg = $("upload-msg");
  bar.hidden = false;
  bar.firstElementChild.style.width = "0";
  msg.className = "sheet-msg";
  msg.textContent = "Đang tải lên…";
  $("submit").disabled = true;

  xhr = new XMLHttpRequest();
  xhr.upload.onprogress = (ev) => {
    if (ev.lengthComputable) bar.firstElementChild.style.width = `${(ev.loaded / ev.total) * 100}%`;
  };
  xhr.onload = async () => {
    const req = xhr;
    xhr = null;
    if (req.status === 201) {
      const m = JSON.parse(req.responseText);
      $("sheet").close();
      toast("Đã tải lên, đang xử lý ở nền");
      await refresh();
      select(m.id);
      return;
    }
    let detail = req.statusText;
    try { detail = JSON.parse(req.responseText).detail; } catch (_) { /* keep statusText */ }
    msg.className = "sheet-msg error";
    msg.textContent = `Lỗi: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`;
    bar.hidden = true;
    $("submit").disabled = false;
  };
  xhr.onerror = xhr.onabort = (ev) => {
    xhr = null;
    bar.hidden = true;
    msg.className = "sheet-msg error";
    msg.textContent = ev.type === "abort" ? "Đã hủy tải lên." : "Lỗi kết nối khi tải lên.";
    $("submit").disabled = false;
  };
  xhr.open("POST", "/api/meetings");
  xhr.send(form);
};

// Drag a file anywhere onto the window.
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth += 1;
  if ($("sheet").open) $("file-pick").classList.add("over");
  else $("drop-overlay").hidden = false;
});
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) { $("drop-overlay").hidden = true; $("file-pick").classList.remove("over"); }
});
window.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  $("drop-overlay").hidden = true;
  $("file-pick").classList.remove("over");
  const file = e.dataTransfer.files[0];
  if ($("sheet").open) setFile(file); else openSheet(file);
});

/* ---------- start ---------- */

function idFromPath() {
  const match = location.pathname.match(/^\/m\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

(async () => {
  await refresh();
  await select(idFromPath(), false);
})();
