# Meeting Transcriber

Web app chạy trên máy (macOS Apple Silicon) để chuyển file ghi âm cuộc họp thành **bản ghi tiếng Việt** có mốc thời gian. Mọi xử lý đều chạy trên máy, audio không gửi ra ngoài.

- Nhận dạng giọng nói: Whisper (`mlx-whisper`, chạy trên GPU Apple), ngôn ngữ cố định tiếng Việt.
- Bỏ qua đoạn im lặng bằng VAD (silero, qua `sherpa-onnx`) và lọc các câu "ảo" Whisper hay sinh ra (câu lặp, "hãy subscribe…").

## Cài đặt (một lần)

Cần [Homebrew](https://brew.sh), Python 3.12 và `uv`.

```bash
brew install ffmpeg uv
git clone https://github.com/khaid2849/meeting-transcriber.git
cd meeting-transcriber
uv sync
./setup_models.sh     # tải model (~3 GB), chạy lại không sao
```

## Chạy

```bash
./run.sh              # mở http://localhost:8765
```

1. Kéo thả file ghi âm (wav, mp3, m4a, mp4… bất kỳ định dạng `ffmpeg` đọc được).
2. Điền tiêu đề, ngày, **từ khóa** (tên riêng, thuật ngữ: giúp Whisper nghe đúng).
3. Đợi xử lý (đóng tab cũng được). Mất khoảng 1/4 thời lượng cuộc họp (đo trên M5: họp 34 phút xử lý trong ~9,5 phút).
4. Mở cuộc họp: bấm mốc thời gian để nghe lại đoạn đó, copy hoặc tải bản ghi.

## Chạy nền như service (khuyên dùng)

```bash
./install-service.sh     # tự chạy khi đăng nhập macOS, tự bật lại nếu bị tắt
./uninstall-service.sh   # gỡ; dữ liệu trong meetings/ vẫn giữ nguyên
```

Sau khi cài, chỉ cần mở http://localhost:8765 (hoặc `./run.sh`, script sẽ mở luôn trình duyệt). Log nằm ở `logs/service.log`.

Tài nguyên (đo trên M5, 16 GB): lúc rảnh ~0% CPU, ~50–200 MB RAM. Lúc xử lý, GPU chạy gần hết công suất và dùng khoảng 4–5 GB RAM; xử lý xong app nhả model ra khỏi bộ nhớ.

## File nằm ở đâu

```
meetings/<ngày>-<tiêu-đề>/
  transcript.md    bản ghi để đọc / đưa cho AI
  audio.m4a        audio đã nén để phát lại
  original.<ext>   file audio gốc (file video thì không giữ)
  meeting.json     tiêu đề, ngày, trạng thái
  whisper.json     kết quả Whisper thô
  segments.json    các câu đã lọc
```

**Đừng sửa tay `transcript.md`**: file này được sinh lại mỗi khi đổi tiêu đề hoặc ngày.

## Hỏi đáp về cuộc họp bằng Claude Code

```bash
cd meetings
claude
> Tóm tắt các ý chính trong cuộc họp 2026-11-02-ha-tang-ai
> Trong các cuộc họp tháng 11, những quyết định nào liên quan tới GPU?
```

## Phát triển

```bash
uv run pytest            # test nhanh
uv run pytest -m slow    # chạy model thật trên 60 giây audio (cần file họp mẫu + model)
uv run python tools/compare_models.py --input file.wav --start 600 --duration 600
```

Thiết kế: `docs/superpowers/specs/`, kế hoạch: `docs/superpowers/plans/`, so sánh model: `docs/model-comparison.md`.
