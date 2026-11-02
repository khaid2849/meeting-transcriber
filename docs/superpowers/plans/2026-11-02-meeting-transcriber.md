# Meeting Transcriber Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local web app: upload a meeting recording → on-device Vietnamese transcript with timestamps and (optional) speaker labels the user can rename → stored as plain Markdown per meeting.

**Architecture:** One FastAPI process serves a vanilla HTML/JS UI and a JSON API, and runs a single background worker thread that processes one job at a time (ffmpeg → VAD → mlx-whisper → sherpa-onnx diarization → merge → `transcript.md`). All state lives on disk under `meetings/<id>/`; no database.

**Tech Stack:** Python 3.12 + `uv`, FastAPI + uvicorn + python-multipart, `mlx-whisper`, `sherpa-onnx` (silero VAD + pyannote-segmentation-3.0 ONNX + speaker embedding ONNX), `numpy`, `ffmpeg`/`ffprobe` (Homebrew), pytest + httpx. Frontend: plain HTML/CSS/JS, no build step.

**Spec:** `docs/superpowers/specs/2026-11-02-meeting-transcriber-design.md`

## Global Constraints

- Project root: `~/Works/meeting-transcriber/` (outside the Odoo workspace). Own git repo; commit messages in English.
- Everything runs on this machine. No network calls at runtime except one-time model downloads during setup. No tokens/accounts (no gated HF models).
- Whisper language fixed to `vi`; `condition_on_previous_text=False`.
- Accept anything `ffmpeg` reads, including video. Work file `audio.m4a`; keep original only if it has no video stream (`original.<ext>`).
- Server: `./run.sh` → `http://localhost:8765`, bound to `127.0.0.1`.
- One job at a time. Server killed mid-job → that job becomes `error` with a retry button; `queued` jobs are re-queued on start.
- Speaker labels default `Người 1`, `Người 2`…; renames live in `meeting.json` and regenerate `transcript.md`; `segments.json` never changes on rename.
- No in-app text editing, no LLM calls, no search/chat (spec §3).
- Diarization failure must never lose the transcript: finish as `done` with a `warning`, unlabeled paragraphs.
- `.gitignore`: `.venv/`, `models/`, `meetings/`, `*.wav`, `__pycache__/`.

## Review Focus

1. **Non-media or corrupt upload** → job ends `error` with a readable message (ffmpeg stderr tail); server keeps running. Test in Task 6.
2. **Long silence / music** → no invented lines ("hãy subscribe…", repeated sentence loops). Tests in Task 4 (`clean_segments`) and VAD gating in Task 5.
3. **Diarization crashes or finds a single speaker** → transcript still produced. Test in Task 6.
4. **Seeking in a 2-hour audio file in the browser** → audio endpoint honours `Range` (206). Test in Task 7.
5. **Vietnamese/duplicate titles** → folder ids are ASCII, unique, stable after title edits. Test in Task 2.

## File Structure

```
pyproject.toml, run.sh, setup_models.sh, README.md, .gitignore
app/config.py        paths, port, model ids, thresholds
app/storage.py       Meeting dataclass, MeetingStore (CRUD on disk), slugify
app/assemble.py      words→speaker utterances, paragraph grouping, Markdown rendering
app/filters.py       VAD region → chunk grouping, hallucination cleanup (pure)
app/audio.py         ffprobe/ffmpeg wrappers, load 16 kHz float32 samples
app/asr.py           silero VAD + mlx-whisper over chunks
app/diarize.py       sherpa-onnx offline speaker diarization
app/worker.py        Engine protocol, Worker thread, pipeline, rebuild_transcript
app/main.py          FastAPI app factory + routes, static pages
app/static/          index.html, meeting.html, app.js, style.css
tools/compare_models.py   one-off model comparison
tests/               unit tests per module; tests/test_ml_integration.py (slow)
```

---

### Task 1: Scaffold, model setup, and model comparison

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `setup_models.sh`, `app/__init__.py`, `app/config.py`, `tools/compare_models.py`, `docs/model-comparison.md`

**Interfaces:**
- Produces: `app/config.py` constants
  `ROOT: Path`, `MEETINGS_DIR = ROOT/"meetings"`, `MODELS_DIR = ROOT/"models"`, `PORT = 8765`,
  `WHISPER_MODEL: str` (chosen in step 6), `LANGUAGE = "vi"`,
  `VAD_MODEL = MODELS_DIR/"silero_vad.onnx"`,
  `SEG_MODEL = MODELS_DIR/"sherpa-onnx-pyannote-segmentation-3-0"/"model.onnx"`,
  `EMB_MODEL = MODELS_DIR/"3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"`,
  `CLUSTER_THRESHOLD = 0.5`, `PARAGRAPH_MAX_GAP = 2.0`, `PARAGRAPH_MAX_LEN = 60.0`, `CHUNK_MAX_LEN = 28.0`.

- [ ] **Step 1:** `git init`; `pyproject.toml` (python `>=3.12,<3.13`; deps: fastapi, uvicorn, python-multipart, mlx-whisper, sherpa-onnx, numpy; dev: pytest, httpx; optional group `compare`: transformers, torch, librosa). `uv sync`. Expected: `uv run python -c "import mlx_whisper, sherpa_onnx"` exits 0.
- [ ] **Step 2:** `setup_models.sh` downloads into `models/` (idempotent, skips existing):
  `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx`,
  `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2` (extract),
  `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` (the URL typo is upstream's).
  Then pre-fetches the chosen Whisper repo via `huggingface_hub.snapshot_download`. Run it. Expected: the three model files exist.
- [ ] **Step 3:** `tools/compare_models.py --input <wav> --start 600 --duration 600 --out tmp/compare/` cuts the excerpt with ffmpeg, transcribes it with each of `mlx-community/whisper-large-v3-mlx` and `mlx-community/whisper-large-v3-turbo` (language `vi`, `condition_on_previous_text=False`), and `vinai/PhoWhisper-large` via transformers on `mps` only if the `compare` group is installed. Writes `<model>.txt` with `[mm:ss] text` lines plus wall-clock seconds.
- [ ] **Step 4:** Run on `cuoc_hop_20261102_143735.wav`. Expected: one txt per model and timings printed.
- [ ] **Step 5:** Read the outputs side by side. Note in `docs/model-comparison.md` per model: runtime, how technical English terms came out, obvious misrecognitions, hallucinations. Pick the most accurate model whose runtime extrapolates to ≤ 30 min for a 2-hour meeting (spec §7).
- [ ] **Step 6:** Set `WHISPER_MODEL` in `app/config.py` to the choice. If PhoWhisper wins, stop and raise it with the user: it changes `app/asr.py` (transformers instead of mlx-whisper).
- [ ] **Step 7:** Commit: `chore: scaffold project, model setup and model comparison`.

### Task 2: Meeting storage

**Files:** Create `app/storage.py`, `tests/test_storage.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class Meeting:
      id: str; title: str; date: str            # "YYYY-MM-DD"
      status: str = "queued"                    # queued|converting|transcribing|diarizing|done|error
      progress: float = 0.0                     # 0..1 within current status
      error: str | None = None; warning: str | None = None
      keywords: str = ""; diarize: bool = True; num_speakers: int | None = None
      speakers: dict[str, str] = field(default_factory=dict)   # "S1" -> "Anh Tuấn"
      duration: float | None = None; model: str | None = None
      original_name: str = ""; created_at: str = ""            # ISO 8601
  def slugify(title: str, date: str) -> str
  class MeetingStore:
      def __init__(self, root: Path)
      def create(self, *, title, date, keywords, diarize, num_speakers, original_name) -> Meeting
      def get(self, meeting_id: str) -> Meeting          # KeyError if missing
      def save(self, meeting: Meeting) -> None           # atomic (tmp + os.replace)
      def list(self) -> list[Meeting]                    # date desc, then created_at desc
      def delete(self, meeting_id: str) -> None
      def dir(self, meeting_id: str) -> Path
      def read_json(self, meeting_id: str, name: str) -> Any       # FileNotFoundError passes through
      def write_json(self, meeting_id: str, name: str, data) -> None  # atomic
  ```

- [ ] **Step 1: Write failing tests**
  ```python
  def test_slugify_strips_vietnamese():
      assert slugify("Hạ tầng AI — hỏi đáp", "2026-11-02") == "2026-11-02-ha-tang-ai-hoi-dap"
  def test_slugify_empty_title():
      assert slugify("", "2026-11-02") == "2026-11-02-cuoc-hop"
  def test_create_dedupes_ids(tmp_path):
      s = MeetingStore(tmp_path)
      a = s.create(title="Họp", date="2026-11-02", keywords="", diarize=True, num_speakers=None, original_name="a.wav")
      b = s.create(title="Họp", date="2026-11-02", keywords="", diarize=True, num_speakers=None, original_name="b.wav")
      assert (a.id, b.id) == ("2026-11-02-hop", "2026-11-02-hop-2")
  def test_roundtrip_and_title_edit_keeps_id(tmp_path): ...  # save title "Khác", get → same id, new title
  def test_list_order(tmp_path): ...       # dates 2026-10-01, 2026-11-02 → newest first
  def test_get_missing_raises(tmp_path): ...  # KeyError
  def test_get_rejects_path_traversal(tmp_path): ...  # get("../x") → KeyError
  def test_delete_removes_dir(tmp_path): ...
  ```
- [ ] **Step 2:** `uv run pytest tests/test_storage.py -v` → FAIL (import error).
- [ ] **Step 3:** Implement. Slug: NFD + drop combining marks, `đ/Đ→d`, lowercase, non-alnum runs → `-`, trim, max 60 chars; empty → `cuoc-hop`. Ids must match `^[a-z0-9-]+$` or `KeyError`.
- [ ] **Step 4:** Tests PASS.
- [ ] **Step 5:** Commit `feat: add on-disk meeting store`.

### Task 3: Transcript assembly and Markdown

**Files:** Create `app/assemble.py`, `tests/test_assemble.py`

**Interfaces:**
- Consumes: `Meeting` (Task 2).
- Whisper segment shape (from Task 5, stored in `whisper.json` as `{"model": str, "segments": [...]}`):
  `{"start": float, "end": float, "text": str, "words": [{"start": float, "end": float, "word": str}]}`
- Turn shape: `tuple[float, float, int]` = (start, end, 0-based speaker index).
- Produces:
  ```python
  def assign_speakers(segments: list[dict], turns: list[tuple[float, float, int]] | None) -> list[dict]
      # -> utterances [{"start", "end", "speaker": "S1"|None, "text"}]
  def speaker_label(speaker: str | None, names: dict[str, str]) -> str | None
  def group_paragraphs(utterances: list[dict], max_gap: float, max_len: float) -> list[dict]
  def format_ts(seconds: float) -> str          # "HH:MM:SS"
  def render_markdown(meeting: Meeting, utterances: list[dict]) -> str
  ```

- [ ] **Step 1: Write failing tests**
  ```python
  def test_assign_without_turns_keeps_segments():
      segs = [seg(0, 4, "Xin chào"), seg(5, 8, "mọi người")]
      assert [u["speaker"] for u in assign_speakers(segs, None)] == [None, None]
  def test_assign_splits_segment_on_speaker_change():
      # words: "Chào"(0-1) "anh"(1-2) | "Dạ"(2.5-3) "vâng"(3-4); turns S1:0-2.2, S2:2.2-4
      out = assign_speakers([seg_words(...)], [(0, 2.2, 0), (2.2, 4, 1)])
      assert [(u["speaker"], u["text"].strip()) for u in out] == [("S1", "Chào anh"), ("S2", "Dạ vâng")]
  def test_word_outside_turns_takes_nearest_turn(): ...  # word at 10-11, turns end at 9.5 → that speaker
  def test_segment_without_words_uses_max_overlap(): ...
  def test_speaker_label_default_and_custom():
      assert speaker_label("S2", {}) == "Người 2"
      assert speaker_label("S2", {"S2": "Anh Tuấn"}) == "Anh Tuấn"
      assert speaker_label("S2", {"S2": "  "}) == "Người 2"
      assert speaker_label(None, {}) is None
  def test_group_merges_same_speaker_and_breaks_on_gap_and_length(): ...
      # same speaker, gap 1s → merged; gap 3s → new; unlabeled run > 60s → split
  def test_format_ts(): assert format_ts(3725.9) == "01:02:05"
  def test_render_markdown():
      md = render_markdown(meeting(title="Hạ tầng AI", date="2026-11-02", duration=2072.25,
                                   speakers={"S1": "Anh Tuấn"}), utts)
      assert md.startswith("# Hạ tầng AI\nNgày: 2026-11-02 · Thời lượng: 00:34:32 · Người nói: Anh Tuấn, Người 2\n")
      assert "**[00:00:05] Anh Tuấn:** Hôm nay" in md
  def test_render_without_speakers(): ...  # paragraphs "**[00:00:05]** text", header omits "Người nói"
  ```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Word → speaker: turn with max overlap, else nearest turn by distance between intervals. Consecutive words with the same speaker form one utterance; text = `"".join(words).strip()`. Speaker index `i` → `f"S{i+1}"`. Speakers list in header ordered by first appearance.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: assemble speaker utterances and render transcript markdown`.

### Task 4: VAD chunking and hallucination cleanup (pure)

**Files:** Create `app/filters.py`, `tests/test_filters.py`

**Interfaces:**
- Produces:
  ```python
  def speech_chunks(regions: list[tuple[float, float]], max_len: float, pad: float = 0.2,
                    total: float | None = None) -> list[tuple[float, float]]
  def clean_segments(segments: list[dict]) -> list[dict]
  ```

- [ ] **Step 1: Write failing tests**
  ```python
  def test_chunks_merge_close_regions_up_to_max_len():
      assert speech_chunks([(0, 5), (5.5, 10), (10.3, 30)], max_len=28) == [(0, 10.2), (10.1, 30.2)]
  def test_chunk_splits_region_longer_than_max_len():
      assert speech_chunks([(0, 60)], max_len=28, pad=0) == [(0, 28), (28, 56), (56, 60)]
  def test_chunks_clamped_to_total():
      assert speech_chunks([(0.1, 9.95)], max_len=28, total=10.0) == [(0, 10.0)]
  def test_clean_drops_known_outro_only_segments():
      segs = [s("Hãy subscribe cho kênh Ghiền Mì Gõ để không bỏ lỡ"), s("Chúng ta bàn về GPU")]
      assert [x["text"] for x in clean_segments(segs)] == ["Chúng ta bàn về GPU"]
  def test_clean_collapses_repeated_segments():
      segs = [s("Cảm ơn."), s("Cảm ơn."), s("Cảm ơn."), s("Tiếp theo")]
      assert [x["text"] for x in clean_segments(segs)] == ["Cảm ơn.", "Tiếp theo"]
  def test_clean_keeps_two_legit_repeats():   # "Đúng rồi." twice → both kept
  def test_clean_collapses_phrase_loop_inside_segment():
      assert clean_segments([s("ok ok và và và và và và")])[0]["text"] == "ok ok và"
  def test_clean_drops_low_confidence_no_speech():
      # no_speech_prob 0.8 & avg_logprob -1.2 → dropped; 0.8 & -0.3 → kept
  ```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Rules: (a) drop a segment whose normalized text (lowercase, strip punctuation) matches an outro pattern list (`subscribe`, `ghiền mì gõ`, `đăng ký kênh`, `like và share`) **and** has ≤ 25 words; (b) a run of ≥ 3 consecutive identical normalized texts keeps the first; (c) a token n-gram (n ≤ 4) repeated ≥ 4 times consecutively within a segment collapses to one occurrence (words list trimmed to match); (d) drop when `no_speech_prob > 0.6 and avg_logprob < -1.0` (fields optional).
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: add VAD chunking and whisper hallucination cleanup`.

### Task 5: ML wrappers (audio, ASR, diarization)

**Files:** Create `app/audio.py`, `app/asr.py`, `app/diarize.py`, `tests/test_audio.py`, `tests/test_ml_integration.py`

**Interfaces:**
- Consumes: `config` (Task 1), `speech_chunks`, `clean_segments` (Task 4).
- Produces:
  ```python
  class AudioError(Exception): ...           # message = last 500 chars of ffmpeg stderr
  def probe(path: Path) -> dict              # {"duration": float, "has_video": bool}; AudioError if unreadable
  def extract_audio(src: Path, dst: Path) -> None     # -vn -ac 1 -c:a aac -b:a 64k → .m4a
  def load_samples(path: Path, sr: int = 16000) -> np.ndarray  # float32 mono
  def transcribe(samples: np.ndarray, model: str, keywords: str,
                 on_progress: Callable[[float], None]) -> list[dict]   # absolute times, cleaned
  def diarize(samples: np.ndarray, num_speakers: int | None,
              on_progress: Callable[[float], None]) -> list[tuple[float, float, int]]
  ```
  `has_video` ignores `attached_pic` streams (album art).

- [ ] **Step 1: Failing tests** (`tests/test_audio.py`, fast; fixtures generated with ffmpeg `sine`/`anullsrc` + `testsrc` into `tmp_path`):
  `test_probe_audio_only`, `test_probe_video_detected`, `test_probe_garbage_raises_audio_error` (text file renamed `.wav`), `test_extract_and_load_16k` (2 s tone → `len(samples) ≈ 32000`, dtype float32).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement `app/audio.py` (subprocess, `ffmpeg -nostdin -y -loglevel error`; `load_samples` reads `-f f32le -ar 16000 -ac 1 -` from stdout). **Step 4:** PASS.
- [ ] **Step 5:** Implement `app/asr.py`. VAD: `sherpa_onnx.VoiceActivityDetector` with `VAD_MODEL`, 16 kHz, `min_silence_duration=0.5`, `min_speech_duration=0.25`, feed in 512-sample windows → regions → `speech_chunks(regions, CHUNK_MAX_LEN, total=len/16000)`. For each chunk call `mlx_whisper.transcribe(chunk_samples, path_or_hf_repo=model, language=LANGUAGE, initial_prompt=keywords or None, condition_on_previous_text=False, word_timestamps=True, verbose=None)`, offset all times by chunk start, call `on_progress(i/len(chunks))`. Return `clean_segments(all)`. Keep `no_speech_prob`/`avg_logprob` on segments.
- [ ] **Step 6:** Implement `app/diarize.py` with `sherpa_onnx.OfflineSpeakerDiarization` (segmentation `SEG_MODEL`, embedding `EMB_MODEL`, `FastClusteringConfig(num_clusters=num_speakers or -1, threshold=CLUSTER_THRESHOLD)`, `min_duration_on=0.3`, `min_duration_off=0.5`); progress callback `(done, total) → on_progress(done/total)` returning 0. Return `[(r.start, r.end, r.speaker) for r in result.sort_by_start_time()]`.
- [ ] **Step 7:** `tests/test_ml_integration.py`, marked `@pytest.mark.slow`, skipped if the meeting wav or `models/` is missing: cut 60 s (`-ss 300 -t 60`) from the meeting wav; `transcribe` returns ≥ 3 segments, all times within [0, 60], non-empty Vietnamese text, progress ends at ~1.0; `diarize` returns ≥ 1 turn with speaker ids ≥ 0. Run `uv run pytest -m slow -v` → PASS. Register the `slow` marker in `pyproject.toml`; default `addopts = "-m 'not slow'"`.
- [ ] **Step 8:** Commit `feat: add ffmpeg, whisper and diarization wrappers`.

### Task 6: Worker and pipeline

**Files:** Create `app/worker.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: `MeetingStore`, `Meeting` (Task 2); `assign_speakers`, `render_markdown` (Task 3); Task 5 functions.
- Produces:
  ```python
  class Engine(Protocol):
      def probe(self, path: Path) -> dict
      def extract_audio(self, src: Path, dst: Path) -> None
      def load_samples(self, path: Path) -> np.ndarray
      def transcribe(self, samples, model: str, keywords: str, on_progress) -> list[dict]
      def diarize(self, samples, num_speakers: int | None, on_progress) -> list[tuple[float, float, int]]
  class RealEngine: ...                       # delegates to app.audio / app.asr / app.diarize
  def rebuild_transcript(store: MeetingStore, meeting_id: str) -> None
      # segments.json + meeting.json -> transcript.md
  class Worker:
      def __init__(self, store: MeetingStore, engine: Engine, model: str)
      def start(self) -> None                 # daemon thread; calls recover() first
      def recover(self) -> None               # converting/transcribing/diarizing -> error("Server bị tắt giữa chừng"); queued -> enqueue full
      def enqueue(self, meeting_id: str, mode: Literal["full", "rediarize"] = "full") -> None
      def run_job(self, meeting_id: str, mode: str) -> None   # synchronous; used by thread and tests
  ```
  Upload file lives at `meetings/<id>/upload.<ext>` until processed.

- [ ] **Step 1: Failing tests** with a `FakeEngine` (canned segments/turns, optional exceptions) and a real `MeetingStore(tmp_path)`:
  ```python
  def test_full_job_writes_all_files_and_done(): ...
      # status done, progress 1.0, model set, duration set; whisper.json, segments.json, transcript.md exist
  def test_audio_upload_kept_as_original_video_deleted(): ...
      # probe has_video False → original.wav exists, upload.* gone; True → no original.*, no upload.*
  def test_bad_media_sets_error_message(): ...      # probe raises AudioError("Invalid data") → status error, error contains "Invalid data"
  def test_diarize_failure_finishes_with_warning(): # diarize raises → status done, warning set, transcript has no speaker names
  def test_diarize_disabled_skips_diarize(): ...    # FakeEngine.diarize never called
  def test_rediarize_reuses_whisper_and_resets_names():
      # after full job, set speakers {"S1": "A"}, rediarize with num_speakers=3 → transcribe not called again, speakers == {}
  def test_rebuild_transcript_uses_names(): ...     # rename S1 → transcript.md contains "Anh Tuấn:"; segments.json unchanged (bytes equal)
  def test_recover_marks_interrupted_and_requeues_queued(): ...
  def test_progress_updates_are_saved(): ...        # FakeEngine calls on_progress(0.5) → a save with status transcribing, progress 0.5 observed
  ```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Status order: `converting` → `transcribing` → `diarizing` (only if enabled) → `done`. Persist progress at most every 2 s and on stage change. Any exception in convert/transcribe → `error` with `str(exc)` (AudioError message) and traceback to the log. Diarization exceptions → `warning="Tách người nói thất bại: …"` and continue with `turns=None`. `rediarize` loads `audio.m4a` + `whisper.json`, clears `speakers`, writes new `segments.json`.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: add background worker and processing pipeline`.

### Task 7: HTTP API

**Files:** Create `app/main.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `MeetingStore`, `Worker.enqueue`, `rebuild_transcript`.
- Produces: `create_app(store: MeetingStore, worker) -> FastAPI` plus module-level `app` built from config with `RealEngine` (worker started on startup).
  Routes:
  - `GET /` → `static/index.html`; `GET /m/{id}` → `static/meeting.html`; `/static/*`.
  - `GET /api/meetings` → `[Meeting as dict]`.
  - `POST /api/meetings` multipart: `file`, `title`, `date`, `keywords=""`, `diarize=true`, `num_speakers` (blank → None) → 201 Meeting; streams the upload to `upload.<ext>` in 1 MB chunks, then `worker.enqueue(id)`.
  - `GET /api/meetings/{id}` → `{"meeting": {...}, "paragraphs": [{"start", "speaker", "label", "text"}]}` (paragraphs empty until done).
  - `PATCH /api/meetings/{id}` JSON `{title?, date?, speakers?}` → updates, `rebuild_transcript` if done → Meeting.
  - `POST /api/meetings/{id}/retry` → only from `error` (else 409), enqueue full.
  - `POST /api/meetings/{id}/rediarize` JSON `{num_speakers?}` → only from `done` (else 409).
  - `DELETE /api/meetings/{id}` → 409 while converting/transcribing/diarizing, else 204.
  - `GET /api/meetings/{id}/audio` → `FileResponse(audio.m4a)` with Range support.
  - `GET /api/meetings/{id}/transcript.md` → download, `text/markdown; charset=utf-8`, filename `<id>.md`.
  - Unknown id → 404.

- [ ] **Step 1: Failing tests** (TestClient, `FakeWorker` recording enqueue calls):
  `test_upload_creates_meeting_and_enqueues`, `test_upload_blank_num_speakers_is_none`, `test_get_unknown_404`, `test_patch_speakers_rebuilds_transcript`, `test_retry_only_from_error_409`, `test_delete_blocked_while_processing_409`, `test_transcript_download_headers`, and
  ```python
  def test_audio_supports_range(client, done_meeting):
      r = client.get(f"/api/meetings/{done_meeting}/audio", headers={"Range": "bytes=0-99"})
      assert r.status_code == 206 and len(r.content) == 100
  ```
- [ ] **Step 2:** Run → FAIL. If the Range test fails with the installed Starlette, pin a Starlette version whose `FileResponse` handles Range, rather than hand-rolling.
- [ ] **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: add HTTP API`.

### Task 8: UI, run script, README

**Files:** Create `app/static/index.html`, `app/static/meeting.html`, `app/static/app.js`, `app/static/style.css`, `run.sh`, `README.md`

**Interfaces:** Consumes the Task 7 routes only. All UI text in Vietnamese.

- [ ] **Step 1:** `index.html`: upload form (drag-and-drop file, title defaulting to the filename without extension, date defaulting to today, keywords, checkbox "Tách người nói" checked, number field "Số người nói (để trống = tự động)"), upload progress bar (XHR `upload.onprogress`), and a meetings table (title link → `/m/{id}`, date, duration `HH:MM:SS`, status in Vietnamese + % for active jobs, warning icon). Poll `/api/meetings` every 2 s while any job is active.
- [ ] **Step 2:** `meeting.html`: editable title/date (save on blur), `<audio>` player, speaker rename table (one input per speaker id seen, placeholder `Người N`, "Lưu tên" → PATCH), paragraphs with clickable `[HH:MM:SS]` that set `audio.currentTime` and play, buttons "Copy bản ghi" (fetch transcript.md → clipboard) and "Tải .md", "Tách lại người nói" (prompt for count, confirm that names will reset), "Chạy lại" when error, "Xóa" (confirm). While processing: status + progress, poll every 2 s.
- [ ] **Step 3:** `run.sh`: checks `ffmpeg` and `models/` (else tells the user to run `./setup_models.sh`), then `uv run uvicorn app.main:app --host 127.0.0.1 --port 8765`, opens the browser. `README.md` (Vietnamese): setup, run, where files live, "đừng sửa tay transcript.md", how to ask Claude Code over `meetings/`.
- [ ] **Step 4:** Manual check: `./run.sh`, upload a 60 s excerpt of the meeting wav, watch progress, rename a speaker, confirm `meetings/<id>/transcript.md` updated, click a timestamp and confirm the audio jumps.
- [ ] **Step 5:** Commit `feat: add web UI and run script`.

### Task 9: End-to-end on the real meeting

- [ ] **Step 1:** Upload `cuoc_hop_20261102_143735.wav` through the UI with keywords `Odoo, Catalog AI, gói thầu, LLM, RAG, GPU`. Record wall-clock time per stage.
- [ ] **Step 2:** Check: `transcript.md` covers 00:00 → ~34:32 with no long unexplained gaps (compare against VAD speech regions), no repeated-loop lines, timestamps match audio at 3 spot checks, speaker count plausible.
- [ ] **Step 3:** Run full suite `uv run pytest -v` and `uv run pytest -m slow -v` → all PASS.
- [ ] **Step 4:** Hand to the user: stage timings, number of speakers found, spot-check notes, and ask them to listen and judge speaker separation. Commit any tuning (`CLUSTER_THRESHOLD`, VAD params) as `chore: tune ... after real-meeting run`.
