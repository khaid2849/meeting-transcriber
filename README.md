# Transcriber

Web app chạy trên máy (macOS Apple Silicon) để chuyển file âm thanh, video hoặc link YouTube thành **văn bản tiếng Việt** có mốc thời gian, rồi hỏi AI về nội dung. Việc chuyển văn bản chạy hoàn toàn trên máy; chỉ khi bạn dùng "Hỏi AI" thì văn bản mới được gửi tới nhà cung cấp AI bạn cấu hình. Giao diện có tiếng Việt và tiếng Anh (nút VI / EN ở góc dưới thanh bên).

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

Khuyên cài thêm `brew install deno`: `yt-dlp` cần một bộ chạy JavaScript để lấy đủ định dạng từ YouTube (không có vẫn chạy, nhưng dễ lỗi khi YouTube thay đổi). Nếu YouTube báo lỗi lạ, cập nhật `yt-dlp`: `uv lock --upgrade-package yt-dlp && uv sync`.

## Chạy

```bash
./run.sh              # mở http://localhost:8765
```

1. Kéo thả file ghi âm hoặc video (wav, mp3, m4a, mp4, mov… bất kỳ định dạng `ffmpeg` đọc được), hoặc dán link video YouTube (⌘V ở bất kỳ đâu trong cửa sổ, hay chọn tab "Link YouTube" khi bấm +). Video YouTube phải công khai hoặc không công khai (unlisted); app chỉ tải phần âm thanh bằng `yt-dlp`.
2. Điền tiêu đề, ngày, **từ khóa** (tên riêng, thuật ngữ: giúp Whisper nghe đúng).
3. Đợi xử lý (đóng tab cũng được). Mất khoảng 1/4 thời lượng bản ghi (đo trên M5: bản ghi 34 phút xử lý trong ~9,5 phút).
4. Mở bản ghi: bấm mốc thời gian để nghe lại đoạn đó, copy hoặc tải văn bản, hoặc bấm "Hỏi AI".

## File nằm ở đâu

```
meetings/<ngày>-<tiêu-đề>/
  transcript.md    văn bản để đọc / đưa cho AI
  audio.m4a        audio đã nén để phát lại
  original.<ext>   file audio gốc (file video thì không giữ)
  meeting.json     tiêu đề, ngày, trạng thái
  whisper.json     kết quả Whisper thô
  segments.json    các câu đã lọc
```

**Đừng sửa tay `transcript.md`**: file này được sinh lại mỗi khi đổi tiêu đề hoặc ngày.

## Hỏi đáp về bản ghi bằng Claude Code

```bash
cd meetings
claude
> Tóm tắt các ý chính trong bản ghi 2026-11-02-ha-tang-ai
> Trong các bản ghi tháng 11, những quyết định nào liên quan tới GPU?
```

## Phát triển

```bash
uv run pytest            # test nhanh
uv run pytest -m slow    # chạy model thật trên 60 giây audio (cần file họp mẫu + model)
uv run python tools/compare_models.py --input file.wav --start 600 --duration 600
```

Thiết kế: `docs/superpowers/specs/`, kế hoạch: `docs/superpowers/plans/`, so sánh model: `docs/model-comparison.md`.
