"use strict";

const STATUS = {
  queued: "Đang chờ",
  converting: "Đang chuyển định dạng",
  transcribing: "Đang chuyển thành văn bản",
  diarizing: "Đang tách người nói",
  done: "Xong",
  error: "Lỗi",
};
const ACTIVE = ["queued", "converting", "transcribing", "diarizing"];
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  for (const c of children) node.append(c);
  return node;
}

function fmtTime(sec) {
  if (sec == null) return "";
  const s = Math.floor(sec);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

function statusText(m) {
  let t = STATUS[m.status] || m.status;
  if (["converting", "transcribing", "diarizing"].includes(m.status) && m.progress > 0) {
    t += ` ${Math.round(m.progress * 100)}%`;
  }
  return t;
}

function statusClass(m) {
  if (m.status === "done") return "status-done";
  if (m.status === "error") return "status-error";
  return "status-active";
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

const jsonBody = (data) => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

/* ---------- index ---------- */

function initIndex() {
  const fileInput = $("file");
  const drop = $("drop");
  const today = new Date();
  $("date").value = new Date(today - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

  function setFile(file) {
    if (!file) return;
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    $("drop-text").textContent = `${file.name} (${(file.size / 1048576).toFixed(1)} MB)`;
    if (!$("title").value) $("title").value = file.name.replace(/\.[^.]+$/, "");
    $("submit").disabled = false;
  }

  $("pick").onclick = (e) => { e.preventDefault(); fileInput.click(); };
  drop.onclick = (e) => { if (e.target === drop) fileInput.click(); };
  fileInput.onchange = () => setFile(fileInput.files[0]);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); setFile(e.dataTransfer.files[0]); };
  $("diarize").onchange = () => { $("num-speakers").disabled = !$("diarize").checked; };

  $("upload-form").onsubmit = (e) => {
    e.preventDefault();
    const file = fileInput.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("title", $("title").value);
    form.append("date", $("date").value);
    form.append("keywords", $("keywords").value);
    form.append("diarize", $("diarize").checked ? "true" : "false");
    form.append("num_speakers", $("diarize").checked ? $("num-speakers").value : "");

    const xhr = new XMLHttpRequest();
    const bar = $("upload-bar");
    bar.hidden = false;
    $("submit").disabled = true;
    $("upload-msg").textContent = "Đang tải lên…";
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) bar.firstElementChild.style.width = `${(ev.loaded / ev.total) * 100}%`;
    };
    xhr.onload = () => {
      bar.hidden = true;
      if (xhr.status === 201) {
        $("upload-form").reset();
        $("date").value = form.get("date");
        $("drop-text").innerHTML = 'Kéo thả file ghi âm vào đây hoặc <a href="#" id="pick">chọn file</a>';
        $("pick").onclick = (ev) => { ev.preventDefault(); fileInput.click(); };
        $("upload-msg").textContent = "Đã tải lên. Đang xử lý ở nền, bạn có thể đóng tab và quay lại sau.";
        loadList();
      } else {
        let detail = xhr.statusText;
        try { detail = JSON.parse(xhr.responseText).detail; } catch (_) { /* keep statusText */ }
        $("upload-msg").textContent = `Lỗi: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`;
        $("submit").disabled = false;
      }
    };
    xhr.onerror = () => {
      bar.hidden = true;
      $("upload-msg").textContent = "Lỗi kết nối khi tải lên.";
      $("submit").disabled = false;
    };
    xhr.open("POST", "/api/meetings");
    xhr.send(form);
  };

  let timer = null;
  async function loadList() {
    clearTimeout(timer);
    let meetings;
    try {
      meetings = await api("/api/meetings");
    } catch (err) {
      $("meetings").replaceChildren(el("tr", {}, el("td", { colSpan: 4, className: "status-error", textContent: err.message })));
      timer = setTimeout(loadList, 5000);
      return;
    }
    const rows = meetings.map((m) => el("tr", {},
      el("td", {}, el("a", { href: `/m/${m.id}`, textContent: m.title })),
      el("td", { textContent: m.date }),
      el("td", { className: "hide-sm", textContent: fmtTime(m.duration) }),
      el("td", {},
        el("span", { className: statusClass(m), textContent: statusText(m) }),
        m.warning ? el("span", { className: "warn", title: m.warning, textContent: " ⚠" }) : "",
      ),
    ));
    $("meetings").replaceChildren(...(rows.length ? rows
      : [el("tr", {}, el("td", { colSpan: 4, className: "muted", textContent: "Chưa có cuộc họp nào." }))]));
    if (meetings.some((m) => ACTIVE.includes(m.status))) timer = setTimeout(loadList, 2000);
  }
  loadList();
}

/* ---------- meeting ---------- */

function initMeeting() {
  const id = decodeURIComponent(location.pathname.split("/").pop());
  const audio = $("audio");
  let meeting = null;
  let timer = null;
  let renderedFor = null;

  async function save(changes) {
    try {
      meeting = await api(`/api/meetings/${id}`, { method: "PATCH", ...jsonBody(changes) });
      return true;
    } catch (err) {
      alert(`Không lưu được: ${err.message}`);
      return false;
    }
  }

  $("title").onchange = () => save({ title: $("title").value }).then((ok) => {
    if (ok) document.title = meeting.title;
    else $("title").value = meeting.title;
  });
  $("date").onchange = () => save({ date: $("date").value }).then((ok) => { if (!ok) $("date").value = meeting.date; });

  $("copy").onclick = async () => {
    // Safari only allows clipboard writes started synchronously in the click, so hand it a promise.
    const blob = fetch(`/api/meetings/${id}/transcript.md`).then((r) => r.blob())
      .then((b) => new Blob([b], { type: "text/plain" }));
    try {
      if (window.ClipboardItem) {
        await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
      } else {
        await navigator.clipboard.writeText(await (await blob).text());
      }
      $("copy").textContent = "Đã copy ✓";
      setTimeout(() => { $("copy").textContent = "Copy bản ghi"; }, 1500);
    } catch (err) {
      alert(`Không copy được (${err.message}). Dùng nút "Tải .md" thay thế.`);
    }
  };
  $("download").onclick = () => { location.href = `/api/meetings/${id}/transcript.md`; };

  $("retry").onclick = async () => {
    await api(`/api/meetings/${id}/retry`, { method: "POST" });
    load();
  };

  $("rediarize").onclick = async () => {
    const answer = prompt("Số người nói (để trống = tự động dò):", meeting.num_speakers || "");
    if (answer === null) return;
    if (Object.keys(meeting.speakers).length
        && !confirm("Tách lại sẽ xóa các tên người nói đã đặt. Tiếp tục?")) return;
    try {
      await api(`/api/meetings/${id}/rediarize`, {
        method: "POST", ...jsonBody({ num_speakers: answer.trim() ? Number(answer) : null }),
      });
    } catch (err) {
      alert(err.message);
    }
    renderedFor = null;
    load();
  };

  $("delete").onclick = async () => {
    if (!confirm(`Xóa cuộc họp "${meeting.title}" và toàn bộ file của nó?`)) return;
    try {
      await api(`/api/meetings/${id}`, { method: "DELETE" });
      location.href = "/";
    } catch (err) {
      alert(err.message);
    }
  };

  $("save-speakers").onclick = async () => {
    const speakers = {};
    for (const input of $("speakers").querySelectorAll("input")) speakers[input.dataset.id] = input.value;
    if (await save({ speakers })) {
      $("speakers-msg").textContent = "Đã lưu, bản ghi đã cập nhật.";
      renderedFor = null;
      load();
    }
  };

  function renderSpeakers(paragraphs) {
    const ids = [...new Set(paragraphs.map((p) => p.speaker).filter(Boolean))];
    $("speakers-card").hidden = !ids.length;
    $("speakers").replaceChildren(...ids.map((sid) => {
      const input = el("input", { type: "text", value: meeting.speakers[sid] || "", placeholder: `Người ${sid.slice(1)}` });
      input.dataset.id = sid;
      return el("label", {}, `Người ${sid.slice(1)}`, input);
    }));
  }

  function renderTranscript(paragraphs) {
    const nodes = paragraphs.map((p) => {
      const ts = el("button", { className: "ts", textContent: `[${fmtTime(p.start)}]` });
      ts.onclick = () => { audio.currentTime = p.start; audio.play(); };
      const node = el("p", { className: "para" }, ts);
      node.dataset.start = p.start;
      if (p.label) node.append(el("span", { className: "who", textContent: `${p.label}:` }));
      node.append(p.text);
      return node;
    });
    $("transcript").replaceChildren(...(nodes.length ? nodes : [el("p", { className: "muted", textContent: "Bản ghi trống." })]));
  }

  audio.ontimeupdate = () => {
    const paras = $("transcript").querySelectorAll(".para");
    let current = null;
    for (const p of paras) {
      if (Number(p.dataset.start) <= audio.currentTime) current = p; else break;
    }
    for (const p of paras) p.classList.toggle("current", p === current);
  };

  async function load() {
    clearTimeout(timer);
    let data;
    try {
      data = await api(`/api/meetings/${id}`);
    } catch (err) {
      $("status").textContent = err.message;
      return;
    }
    meeting = data.meeting;
    document.title = meeting.title;
    if (document.activeElement !== $("title")) $("title").value = meeting.title;
    if (document.activeElement !== $("date")) $("date").value = meeting.date;
    const meta = [];
    if (meeting.duration) meta.push(`Thời lượng ${fmtTime(meeting.duration)}`);
    if (meeting.model) meta.push(meeting.model.split("/").pop());
    if (meeting.original_name) meta.push(meeting.original_name);
    $("meta").textContent = meta.join(" · ");

    const active = ACTIVE.includes(meeting.status);
    const status = $("status");
    status.className = `msg ${statusClass(meeting)}`;
    status.textContent = meeting.status === "error" ? `Lỗi: ${meeting.error}`
      : meeting.status === "done" ? "" : statusText(meeting);
    if (meeting.status === "done" && meeting.warning) {
      status.className = "msg warn";
      status.textContent = `⚠ ${meeting.warning}`;
    }
    $("bar").hidden = !active;
    $("bar").firstElementChild.style.width = `${Math.round((meeting.progress || 0) * 100)}%`;

    const done = meeting.status === "done";
    $("retry").hidden = meeting.status !== "error";
    $("copy").disabled = $("download").disabled = $("rediarize").disabled = !done;
    $("delete").disabled = ["converting", "transcribing", "diarizing"].includes(meeting.status);

    const hasAudio = done || meeting.status === "diarizing";
    $("player").hidden = !hasAudio;
    if (hasAudio && !audio.src) audio.src = `/api/meetings/${id}/audio`;

    if (done && renderedFor !== JSON.stringify(meeting.speakers)) {
      renderSpeakers(data.paragraphs);
      renderTranscript(data.paragraphs);
      renderedFor = JSON.stringify(meeting.speakers);
    } else if (!done) {
      $("speakers-card").hidden = true;
      $("transcript").replaceChildren(el("p", { className: "muted",
        textContent: active ? "Bản ghi sẽ hiện ở đây khi xử lý xong." : "Chưa có bản ghi." }));
      renderedFor = null;
    }
    if (active) timer = setTimeout(load, 2000);
  }
  load();
}

if (document.body.dataset.page === "index") initIndex();
else initMeeting();
