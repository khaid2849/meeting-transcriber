# Meeting Transcriber

Web app chạy trên máy (macOS Apple Silicon) để chuyển file ghi âm cuộc họp thành **bản ghi tiếng Việt** có mốc thời gian và tách người nói. Mọi xử lý đều chạy trên máy, audio không gửi ra ngoài.

- Nhận dạng giọng nói: Whisper (`mlx-whisper`, chạy trên GPU Apple), ngôn ngữ cố định tiếng Việt.
- Tách người nói: `sherpa-onnx` (pyannote segmentation + speaker embedding), không cần token/tài khoản.
- Bỏ qua đoạn im lặng bằng VAD và lọc các câu "ảo" Whisper hay sinh ra (câu lặp, "hãy subscribe…").

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
2. Điền tiêu đề, ngày, **từ khóa** (tên riêng, thuật ngữ: giúp Whisper nghe đúng), chọn tách người nói.
3. Đợi xử lý (đóng tab cũng được). Cuộc họp 1 tiếng mất khoảng 10–15 phút.
4. Mở cuộc họp: đổi "Người 1", "Người 2"… thành tên thật, bấm mốc thời gian để nghe lại đoạn đó, copy hoặc tải bản ghi.

## File nằm ở đâu

```
meetings/<ngày>-<tiêu-đề>/
  transcript.md    bản ghi để đọc / đưa cho AI
  audio.m4a        audio đã nén để phát lại
  original.<ext>   file audio gốc (file video thì không giữ)
  meeting.json     tiêu đề, ngày, trạng thái, tên người nói
  whisper.json     kết quả Whisper thô
  segments.json    câu đã gắn người nói
```

**Đừng sửa tay `transcript.md`**: file này được sinh lại mỗi khi đổi tên người nói.

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
