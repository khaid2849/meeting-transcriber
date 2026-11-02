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
  tab: "meetings",
  pendingSeek: null,
  chats: [],
  chat: null, // full chat detail of the open conversation
  providers: { providers: [], default_id: null },
  streaming: null, // AbortController while an answer streams
  inflight: null, // {chatId, answer} for the answer being streamed
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
  $("ask").disabled = true;
}

async function select(id, push = true) {
  if (push && (id !== state.selected || state.tab !== "meetings")) history.pushState({}, "", id ? `/m/${id}` : "/");
  setTab("meetings");
  showPane("meeting");
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
    state.pendingSeek = null;
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
  $("copy").disabled = $("download").disabled = $("ask").disabled = !done;
  $("delete").disabled = RUNNING.includes(m.status);
  $("player").hidden = !done;
  if (done && !audio.getAttribute("src")) {
    audio.src = `/api/meetings/${m.id}/audio`;
    updatePlayer();
  }
  if (done && state.pendingSeek != null) {
    const t = state.pendingSeek;
    state.pendingSeek = null;
    const seek = () => { audio.currentTime = t; audio.play().catch(() => {}); };
    if (audio.readyState >= 1) seek(); else audio.addEventListener("loadedmetadata", seek, { once: true });
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
  if (e.code === "Space" && !typing && !$("player").hidden && state.tab === "meetings") {
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

const imeEnter = (e) => e.isComposing || e.keyCode === 229; // Safari commits Telex/VNI words with Enter

$("title").onkeydown = (e) => { if (imeEnter(e)) return; if (e.key === "Enter") e.target.blur(); if (e.key === "Escape") { e.target.value = state.meeting.title; e.target.blur(); } };
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
$("ask").onclick = () => newChat([state.selected]);
window.onpopstate = () => route(false);

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

$("new").onclick = () => (state.tab === "chats" ? openPicker(null) : openSheet());
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

/* ---------- tabs & panes ---------- */

const PROVIDER_NAMES = { anthropic: "Claude", openai: "OpenAI", gemini: "Gemini", openai_compat: "Tương thích OpenAI" };
const COMPAT_PRESETS = [
  ["Alibaba Qwen (quốc tế)", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen-max"],
  ["Alibaba Qwen (Trung Quốc)", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-max"],
  ["DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"],
  ["OpenRouter", "https://openrouter.ai/api/v1", ""],
  ["Groq", "https://api.groq.com/openai/v1", ""],
  ["Mistral", "https://api.mistral.ai/v1", ""],
  ["Together AI", "https://api.together.xyz/v1", ""],
  ["Ollama (trên máy)", "http://localhost:11434/v1", ""],
  ["LM Studio (trên máy)", "http://localhost:1234/v1", ""],
];
const SMALL_CONTEXT_TOKENS = 28000;
const DEFAULT_MODELS = { anthropic: "claude-opus-5-5" };
const SUGGESTIONS = [
  ["Tóm tắt", "Tóm tắt nội dung chính: chủ đề, các ý quan trọng và kết luận."],
  ["Các quyết định", "Liệt kê các quyết định đã được chốt, kèm nguồn."],
  ["Việc cần làm", "Liệt kê các việc cần làm (ai làm, làm gì, hạn nếu có) và các câu hỏi còn bỏ ngỏ."],
  ["Điểm chưa rõ", "Những điểm nào còn tranh luận hoặc chưa rõ ràng?"],
];

function setTab(tab) {
  state.tab = tab;
  $("tab-meetings").classList.toggle("on", tab === "meetings");
  $("tab-chats").classList.toggle("on", tab === "chats");
  $("list").hidden = tab !== "meetings";
  $("chat-list").hidden = tab !== "chats";
  $("drop-hint").hidden = tab !== "meetings";
  $("new").title = tab === "chats" ? "Cuộc trò chuyện mới" : "Tải file ghi âm lên";
}

function showPane(kind) {
  $("detail").hidden = kind === "chat";
  $("chat-pane").hidden = kind !== "chat";
}

function reveal(tab, url, open) {
  history.pushState({}, "", url);
  setTab(tab);
  showPane(tab === "chats" ? "chat" : "meeting");
  $("window").classList.toggle("show-detail", open);
  if (tab === "chats") renderChatList(); else renderList();
}

$("tab-meetings").onclick = () => {
  if (state.tab === "meetings") return;
  if (state.meeting) reveal("meetings", `/m/${state.meeting.id}`, true);
  else select(null);
};
$("tab-chats").onclick = () => {
  if (state.tab === "chats") return;
  if (state.chat) reveal("chats", `/c/${state.chat.id}`, true);
  else selectChat(null);
};

/* ---------- chats list ---------- */

async function refreshChats() {
  try {
    state.chats = await api("/api/chats");
  } catch (err) {
    $("chat-list").replaceChildren(el("div", { className: "list-empty", textContent: err.message }));
    return;
  }
  renderChatList();
}

function renderChatList() {
  const list = $("chat-list");
  if (!state.chats.length) {
    list.replaceChildren(el("div", { className: "list-empty", textContent: "Chưa có cuộc trò chuyện nào" }));
    return;
  }
  const nodes = [];
  let lastGroup = null;
  for (const c of state.chats) {
    const day = c.updated_at.slice(0, 10);
    const group = groupLabel(day);
    if (group !== lastGroup) {
      nodes.push(el("div", { className: "group", textContent: group }));
      lastGroup = group;
    }
    const n = c.meeting_ids.length;
    const item = el("a", { className: "item", href: `/c/${c.id}` },
      el("div", { className: "t", textContent: c.title }),
      el("div", { className: "sub" },
        el("span", { textContent: shortDate(day) }),
        el("span", { className: "chip-count", textContent: n === 1 ? "1 cuộc họp" : `${n} cuộc họp` })),
    );
    if (c.id === state.chat?.id) item.classList.add("selected");
    item.onclick = (e) => { e.preventDefault(); selectChat(c.id); };
    nodes.push(item);
  }
  list.replaceChildren(...nodes);
}

/* ---------- chat pane ---------- */

async function selectChat(id, push = true) {
  if (push && (id !== state.chat?.id || state.tab !== "chats")) history.pushState({}, "", id ? `/c/${id}` : "/c");
  if (state.streaming && state.chat?.id !== id) state.streaming.abort();
  setTab("chats");
  showPane("chat");
  $("window").classList.toggle("show-detail", !!id);
  state.chat = null;
  if (id) {
    try {
      state.chat = await api(`/api/chats/${id}`);
    } catch (err) {
      toast(err.message);
    }
  }
  document.title = state.chat ? state.chat.title : "Hỏi AI";
  renderChatList();
  renderChat();
  scrollChatToBottom(true);
  if (state.chat && !state.chat.messages.length) $("prompt").focus();
}

async function reloadChat() {
  if (!state.chat) return;
  let chat;
  try {
    chat = await api(`/api/chats/${state.chat.id}`);
  } catch (err) {
    toast(err.message);
    return;
  }
  // The server has saved the question but not yet the answer that is still streaming.
  if (state.inflight?.chatId === chat.id) chat.messages.push(state.inflight.answer);
  state.chat = chat;
  renderChat();
}

async function newChat(meetingIds) {
  try {
    const chat = await api("/api/chats", { method: "POST", ...jsonBody({ meeting_ids: meetingIds }) });
    await refreshChats();
    await selectChat(chat.id);
  } catch (err) {
    toast(err.message);
  }
}

function providerFor(chat) {
  const list = state.providers.providers;
  return list.find((p) => p.id === chat.provider_id) || list.find((p) => p.id === state.providers.default_id) || null;
}

function formatTokens(n) {
  if (n < 1000) return `${n} token`;
  return `${Math.round(n / 1000).toLocaleString("vi-VN")} nghìn token`;
}

function renderChat() {
  const chat = state.chat;
  const has = !!chat;
  $("chat-title").hidden = $("chat-provider").hidden = $("chat-delete").hidden = !has;
  $("chat-scope").hidden = !has;
  $("composer").parentElement.hidden = !has;
  if (!has) {
    $("messages").replaceChildren(el("div", { className: "empty" },
      el("p", { textContent: state.chats.length ? "Chọn một cuộc trò chuyện ở bên trái" : "Hỏi AI về nội dung các cuộc họp đã ghi" }),
      Object.assign(el("button", { className: "primary-btn", textContent: "Cuộc trò chuyện mới" }), { onclick: () => openPicker(null) })));
    return;
  }
  if (document.activeElement !== $("chat-title")) $("chat-title").value = chat.title;

  const select = $("chat-provider");
  const providers = state.providers.providers;
  const current = providerFor(chat);
  select.replaceChildren(...(providers.length
    ? providers.map((p) => el("option", { value: p.id, textContent: `${p.name} · ${p.model}`, selected: p.id === current?.id }))
    : [el("option", { value: "", textContent: "Chưa cấu hình AI" })]));
  select.disabled = !providers.length;

  const usable = chat.meetings.filter((m) => m.usable);
  const chips = chat.meetings.map((m) => {
    const name = m.title || "Cuộc họp đã xóa";
    const chip = el("span", { className: `chip${m.usable ? "" : " off"}`, title: m.usable ? `${name} · ${m.date}` : "Không dùng được: cuộc họp đã xóa hoặc chưa xử lý xong" },
      m.label ? el("span", { className: "label", textContent: m.label }) : null,
      el("span", { className: "name", textContent: name }));
    const remove = el("button", { textContent: "×", title: "Bỏ khỏi cuộc trò chuyện", type: "button" });
    remove.onclick = () => updateScope(chat.meeting_ids.filter((x) => x !== m.id));
    chip.append(remove);
    return chip;
  });
  const add = el("button", { className: "add", type: "button", textContent: chat.meetings.length ? "+ Thêm cuộc họp" : "+ Chọn cuộc họp" });
  add.onclick = () => openPicker(chat);
  const tokens = el("span", { className: "tokens", textContent: usable.length ? `≈ ${formatTokens(chat.estimated_tokens)} mỗi câu hỏi` : "" });
  $("chat-scope").replaceChildren(...chips, add, tokens);

  const notice = $("chat-notice");
  notice.classList.remove("warn");
  if (!providers.length) {
    const open = el("button", { className: "secondary-btn", type: "button", textContent: "Mở cài đặt" });
    open.onclick = openSettings;
    notice.replaceChildren(el("span", { textContent: "Chưa có cấu hình AI. Thêm API key của Claude, OpenAI hoặc Gemini để bắt đầu hỏi." }), open);
    notice.hidden = false;
  } else if (!usable.length) {
    notice.replaceChildren(el("span", { textContent: "Chưa có cuộc họp nào (đã xử lý xong) trong cuộc trò chuyện này." }));
    notice.hidden = false;
  } else if (current?.provider === "openai_compat" && chat.estimated_tokens > SMALL_CONTEXT_TOKENS) {
    notice.classList.add("warn");
    notice.replaceChildren(el("span", { textContent: `Bản ghi khoảng ${formatTokens(chat.estimated_tokens)}. Model chạy trên máy hoặc model nhỏ thường chỉ nhận 8–32 nghìn token; nếu bị lỗi hoặc trả lời thiếu, hãy bớt cuộc họp.` }));
    notice.hidden = false;
  } else {
    notice.hidden = true;
  }


  $("suggestions").replaceChildren(...(chat.messages.length || !usable.length || !providers.length ? [] : SUGGESTIONS.map(([label, prompt]) => {
    const b = el("button", { type: "button", textContent: label, title: prompt });
    b.onclick = () => send(prompt);
    return b;
  })));
  renderMessages();
  updateComposer();
}

function renderMessages() {
  const chat = state.chat;
  const box = $("messages");
  if (!chat.messages.length) {
    const n = chat.meetings.filter((m) => m.usable).length;
    box.replaceChildren(el("div", { className: "chat-empty", textContent: n ? `Hỏi bất cứ điều gì về ${n === 1 ? "cuộc họp" : `${n} cuộc họp`} đã chọn.` : "" }));
    return;
  }
  box.replaceChildren(...chat.messages.map(messageNode));
}

function messageNode(m) {
  if (m.role === "user") return el("div", { className: "msg-user", textContent: m.content });
  const node = el("div", { className: "msg-ai" });
  const md = el("div", { className: `md${m.streaming ? " typing" : ""}` });
  md.append(renderMarkdown(m.content, m.labels || state.chat.labels));
  node.append(md);
  if (m.error) node.append(el("div", { className: "msg-error", textContent: `⚠ ${m.error}` }));
  if (!m.streaming && m.content) {
    const copy = el("button", { type: "button", textContent: "Copy" });
    copy.onclick = () => navigator.clipboard.writeText(m.content).then(() => toast("Đã copy câu trả lời"), () => toast("Không copy được"));
    node.append(el("div", { className: "msg-meta" }, el("span", { textContent: [m.provider, m.model].filter(Boolean).join(" · ") }), copy));
  }
  return node;
}

// LLM output is untrusted (a transcript can carry a prompt injection): text formatting only, no images or forms.
const MD_TAGS = ["p", "br", "strong", "em", "b", "i", "s", "del", "code", "pre", "blockquote", "ul", "ol", "li",
  "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td", "a", "hr"];
const CITE = /\[(M\d+)\s+((?:\d{1,2}:)?\d{1,2}:\d{2}(?:\s*[,;–-]\s*(?:\d{1,2}:)?\d{1,2}:\d{2})*)\]/g;

function toSeconds(ts) {
  return ts.split(":").map(Number).reduce((acc, n) => acc * 60 + n, 0);
}

function renderMarkdown(text, labels) {
  const linked = (text || "").replace(CITE, (all, label, times) => {
    const id = labels?.[label];
    if (!id) return all;
    const secs = toSeconds(times.split(/[,;–-]/)[0].trim());
    return `[${label} ${times}](/m/${encodeURIComponent(id)}?t=${secs})`;
  });
  const frag = DOMPurify.sanitize(marked.parse(linked, { gfm: true, breaks: true }), {
    ALLOWED_TAGS: MD_TAGS, ALLOWED_ATTR: ["href", "title"], ALLOWED_URI_REGEXP: /^(?:https?:\/\/|\/m\/)/i,
    RETURN_DOM_FRAGMENT: true,
  });
  for (const a of frag.querySelectorAll("a[href]")) {
    const href = a.getAttribute("href");
    if (href.startsWith("/m/")) {
      a.className = "cite";
      const id = decodeURIComponent(href.slice(3).split("?")[0]);
      a.title = state.meetings.find((x) => x.id === id)?.title || "";
    } else {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    }
  }
  return frag;
}

$("messages").addEventListener("click", (e) => {
  const a = e.target.closest("a.cite");
  if (!a) return;
  e.preventDefault();
  const url = new URL(a.href);
  state.pendingSeek = Number(url.searchParams.get("t")) || 0;
  select(decodeURIComponent(url.pathname.slice(3)));
});

function nearBottom() {
  const s = $("chat-scroll");
  return s.scrollHeight - s.scrollTop - s.clientHeight < 80;
}

function scrollChatToBottom(force) {
  const s = $("chat-scroll");
  if (force || nearBottom()) s.scrollTop = s.scrollHeight;
}

/* ---------- sending ---------- */

function updateComposer() {
  const streaming = !!state.streaming;
  const ready = state.chat && state.providers.providers.length && state.chat.meetings.some((m) => m.usable);
  $("send").classList.toggle("stop", streaming);
  $("send").setAttribute("aria-label", streaming ? "Dừng" : "Gửi");
  $("send").disabled = !streaming && !(ready && $("prompt").value.trim());
}

function autosize() {
  const t = $("prompt");
  t.style.height = "auto";
  t.style.height = `${Math.min(t.scrollHeight, 180)}px`;
}

$("prompt").addEventListener("input", () => { autosize(); updateComposer(); });
$("prompt").addEventListener("keydown", (e) => {
  if (imeEnter(e)) return;
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (!$("send").disabled && !state.streaming) send($("prompt").value);
  }
});
$("composer").onsubmit = (e) => {
  e.preventDefault();
  if (state.streaming) state.streaming.abort();
  else send($("prompt").value);
};

let paintQueued = false;
function paintStreaming(answer) {
  if (paintQueued) return;
  paintQueued = true;
  requestAnimationFrame(() => {
    paintQueued = false;
    const nodes = $("messages").querySelectorAll(".msg-ai");
    const last = nodes[nodes.length - 1];
    if (!last) return;
    const stick = nearBottom();
    last.replaceWith(messageNode(answer));
    if (stick) scrollChatToBottom(true);
  });
}

async function send(text) {
  const chat = state.chat;
  const question = (text || "").trim();
  if (!chat || !question || state.streaming) return;
  const provider = providerFor(chat);
  $("prompt").value = "";
  autosize();
  const answer = { role: "assistant", content: "", streaming: true, provider: provider?.name, model: provider?.model };
  chat.messages.push({ role: "user", content: question }, answer);
  $("suggestions").replaceChildren();
  renderMessages();
  scrollChatToBottom(true);

  const ctrl = new AbortController();
  state.streaming = ctrl;
  state.inflight = { chatId: chat.id, answer };
  updateComposer();
  let failedEarly = false;
  try {
    const res = await fetch(`/api/chats/${chat.id}/messages`, { method: "POST", ...jsonBody({ content: question }), signal: ctrl.signal });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
      failedEarly = true;
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.delta) answer.content += ev.delta;
        if (ev.error) answer.error = ev.error;
      }
      paintStreaming(answer);
    }
  } catch (err) {
    answer.error = err.name === "AbortError" ? "Đã dừng giữa chừng" : err.message;
    if (failedEarly) toast(err.message);
  } finally {
    answer.streaming = false;
    state.streaming = null;
    state.inflight = null;
    if (state.chat?.id === chat.id) {
      if (failedEarly) {
        chat.messages.splice(-2, 2);
        $("prompt").value = question;
        autosize();
        renderChat();
      } else {
        await reloadChat();
      }
      scrollChatToBottom(false);
    }
    updateComposer();
    refreshChats();
  }
}

/* ---------- chat toolbar ---------- */

async function patchChat(changes) {
  try {
    await api(`/api/chats/${state.chat.id}`, { method: "PATCH", ...jsonBody(changes) });
  } catch (err) {
    toast(`Không lưu được: ${err.message}`);
    return false;
  }
  await reloadChat();
  refreshChats();
  return true;
}

function updateScope(meetingIds) { return patchChat({ meeting_ids: meetingIds }); }

$("chat-title").onkeydown = (e) => {
  if (imeEnter(e)) return;
  if (e.key === "Enter") e.target.blur();
  if (e.key === "Escape") { e.target.value = state.chat.title; e.target.blur(); }
};
$("chat-title").onchange = () => {
  if ($("chat-title").value.trim()) patchChat({ title: $("chat-title").value.trim() });
  else $("chat-title").value = state.chat.title;
};
$("chat-provider").onchange = () => patchChat({ provider_id: $("chat-provider").value });
$("chat-delete").onclick = async () => {
  if (!confirm(`Xóa cuộc trò chuyện "${state.chat.title}"?`)) return;
  try {
    await api(`/api/chats/${state.chat.id}`, { method: "DELETE" });
  } catch (err) {
    toast(err.message);
    return;
  }
  await refreshChats();
  selectChat(null);
};
$("chat-back").onclick = () => selectChat(null);

/* ---------- meeting picker ---------- */

let pickerFor = null; // chat being edited, or null for a new chat

function openPicker(chat) {
  pickerFor = chat;
  const chosen = new Set(chat ? chat.meeting_ids : []);
  const done = state.meetings.filter((m) => m.status === "done");
  $("picker-title").textContent = chat ? "Cuộc họp trong cuộc trò chuyện" : "Chọn cuộc họp để hỏi";
  $("picker-msg").textContent = done.length ? "" : "Chưa có cuộc họp nào xử lý xong.";
  $("pick-list").replaceChildren(...done.map((m) => el("label", {},
    el("input", { type: "checkbox", value: m.id, checked: chosen.has(m.id) }),
    el("span", { textContent: m.title }),
    el("span", { className: "when", textContent: `${shortDate(m.date)} · ${clock(m.duration)}` }))));
  $("picker-ok").textContent = chat ? "Lưu" : "Bắt đầu";
  $("picker").showModal();
}

$("picker-cancel").onclick = () => $("picker").close();
$("picker-form").onsubmit = async (e) => {
  e.preventDefault();
  const ids = [...$("pick-list").querySelectorAll("input:checked")].map((i) => i.value);
  if (!pickerFor && !ids.length) {
    $("picker-msg").textContent = "Chọn ít nhất một cuộc họp.";
    return;
  }
  $("picker").close();
  if (pickerFor) {
    // Meetings not offered in the list (deleted or still processing) stay; their chip has its own ×.
    const offered = new Set(state.meetings.filter((m) => m.status === "done").map((m) => m.id));
    await updateScope([...ids, ...pickerFor.meeting_ids.filter((id) => !offered.has(id))]);
  } else {
    await newChat(ids);
  }
};

/* ---------- AI settings ---------- */

let editing = null; // provider being edited, or null when adding

async function loadProviders() {
  try {
    state.providers = await api("/api/ai/providers");
  } catch (err) {
    toast(err.message);
  }
}

function openSettings() {
  loadProviders().then(renderProviderList);
  closeProviderForm();
  $("settings").showModal();
}

function renderProviderList() {
  const { providers, default_id } = state.providers;
  $("provider-list").replaceChildren(...providers.map((p) => {
    const actions = [];
    if (p.id === default_id) {
      actions.push(el("span", { className: "badge", textContent: "Mặc định" }));
    } else {
      const def = el("button", { className: "link", type: "button", textContent: "Đặt mặc định" });
      def.onclick = async () => {
        try {
          state.providers = await api("/api/ai/default", { method: "POST", ...jsonBody({ id: p.id }) });
        } catch (err) {
          alert(err.message);
        }
        renderProviderList();
      };
      actions.push(def);
    }
    const edit = el("button", { className: "link", type: "button", textContent: "Sửa" });
    edit.onclick = () => openProviderForm(p);
    const del = el("button", { className: "link danger", type: "button", textContent: "Xóa" });
    del.onclick = async () => {
      if (!confirm(`Xóa cấu hình "${p.name}"?`)) return;
      try {
        await api(`/api/ai/providers/${p.id}`, { method: "DELETE" });
      } catch (err) {
        alert(err.message);
      }
      await loadProviders();
      renderProviderList();
    };
    return el("div", { className: "prov" },
      el("div", { className: "info" }, el("b", { textContent: p.name }),
        el("span", { textContent: [PROVIDER_NAMES[p.provider], p.base_url && new URL(p.base_url).host, p.model, p.key_hint].filter(Boolean).join(" · ") })),
      ...actions, edit, del);
  }));
  if (!providers.length && $("provider-form").hidden) openProviderForm(null);
}

function openProviderForm(p) {
  editing = p;
  $("pf-heading").textContent = p ? `Sửa "${p.name}"` : "Thêm cấu hình";
  $("pf-name").value = p ? p.name : "";
  $("pf-provider").value = p ? p.provider : "anthropic";
  $("pf-provider").disabled = !!p;
  $("pf-base").value = p?.base_url || "";
  $("pf-preset").value = "";
  $("pf-key").value = "";
  $("pf-model").value = p ? p.model : DEFAULT_MODELS.anthropic;
  syncProviderFields();
  $("pf-models").replaceChildren();
  $("pf-msg").textContent = "";
  $("pf-msg").className = "sheet-msg";
  $("provider-form").hidden = false;
  $("settings-actions").hidden = true;
  $("pf-name").focus();
}

function closeProviderForm() {
  $("provider-form").hidden = true;
  $("settings-actions").hidden = false;
}

function formMessage(text, error = false) {
  $("pf-msg").textContent = text;
  $("pf-msg").className = `sheet-msg${error ? " error" : ""}`;
}

function syncProviderFields() {
  const compat = $("pf-provider").value === "openai_compat";
  $("pf-compat").hidden = !compat;
  $("pf-key").placeholder = editing
    ? `Để trống để giữ key hiện tại${editing.key_hint ? ` (${editing.key_hint})` : ""}`
    : compat ? "Không bắt buộc với Ollama / LM Studio" : "Dán API key";
}

$("pf-preset").replaceChildren($("pf-preset").firstElementChild,
  ...COMPAT_PRESETS.map(([name, url], i) => el("option", { value: String(i), textContent: `${name} · ${new URL(url).host}` })));
$("pf-preset").onchange = () => {
  const preset = COMPAT_PRESETS[Number($("pf-preset").value)];
  if (!preset || $("pf-preset").value === "") return;
  const [name, url, model] = preset;
  $("pf-base").value = url;
  if (!$("pf-name").value.trim()) $("pf-name").value = name;
  if (model && !$("pf-model").value.trim()) $("pf-model").value = model;
};
$("pf-provider").onchange = () => {
  $("pf-model").value = DEFAULT_MODELS[$("pf-provider").value] || "";
  $("pf-models").replaceChildren();
  syncProviderFields();
};
$("pf-load").onclick = async () => {
  const key = $("pf-key").value.trim();
  const compat = $("pf-provider").value === "openai_compat";
  const base = $("pf-base").value.trim();
  const body = editing
    ? { provider_id: editing.id, api_key: key || null, base_url: compat ? base || null : null }
    : { provider: $("pf-provider").value, api_key: key, base_url: compat ? base : null };
  if (compat && !base) { formMessage("Nhập Base URL trước để tải danh sách model.", true); return; }
  if (!editing && !compat && !key) { formMessage("Nhập API key trước để tải danh sách model.", true); return; }
  formMessage("Đang tải danh sách model…");
  try {
    const { models } = await api("/api/ai/models", { method: "POST", ...jsonBody(body) });
    $("pf-models").replaceChildren(...models.map((m) => el("option", { value: m })));
    formMessage(`Đã tải ${models.length} model. Bấm vào ô Model để chọn.`);
  } catch (err) {
    formMessage(err.message, true);
  }
};
$("pf-cancel").onclick = closeProviderForm;
$("provider-form").onsubmit = async (e) => {
  e.preventDefault();
  const body = { name: $("pf-name").value, model: $("pf-model").value, api_key: $("pf-key").value };
  if ($("pf-provider").value === "openai_compat") body.base_url = $("pf-base").value;
  try {
    if (editing) {
      await api(`/api/ai/providers/${editing.id}`, { method: "PATCH", ...jsonBody(body) });
    } else {
      await api("/api/ai/providers", { method: "POST", ...jsonBody({ ...body, provider: $("pf-provider").value }) });
    }
  } catch (err) {
    formMessage(err.message, true);
    return;
  }
  closeProviderForm();
  await loadProviders();
  renderProviderList();
  toast("Đã lưu cấu hình AI");
};
$("open-settings").onclick = openSettings;
$("pf-add").onclick = () => openProviderForm(null);
$("settings-close").onclick = () => $("settings").close();
$("settings").addEventListener("close", () => { if (state.chat) renderChat(); });

/* ---------- start ---------- */

function route(push) {
  if (location.pathname === "/c") return selectChat(null, push);
  const m = location.pathname.match(/^\/(m|c)\/([^/]+)$/);
  const seek = Number(new URLSearchParams(location.search).get("t"));
  if (m && m[1] === "m" && Number.isFinite(seek) && seek > 0) state.pendingSeek = seek;
  if (m && m[1] === "c") return selectChat(decodeURIComponent(m[2]), push);
  return select(m ? decodeURIComponent(m[2]) : null, push);
}

(async () => {
  await Promise.all([refresh(), refreshChats(), loadProviders()]);
  await route(false);
})();
