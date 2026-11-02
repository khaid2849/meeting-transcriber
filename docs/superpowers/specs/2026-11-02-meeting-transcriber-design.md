# Meeting Transcriber — Thiết kế

Ngày: 2026-11-02 · Trạng thái: đã duyệt, đã triển khai. Cập nhật 2026-11-02: bỏ tách người nói (xem §6).

## 1. Mục đích và lý do

Team đang build các module dùng AI (Catalog AI, đánh giá chấm điểm gói thầu…) và có nhiều cuộc họp hỏi đáp kinh nghiệm (ví dụ: xây hạ tầng AI). Sẽ còn nhiều cuộc họp nữa. Người dùng cần **nắm lại nội dung các cuộc họp** mà không phải nghe lại hàng giờ audio.

Cách người dùng muốn làm: có **bản ghi văn bản đầy đủ, chính xác** của cuộc họp, rồi tự dùng công cụ AI (Claude) để hỏi đáp và lấy overview. Vì vậy giá trị cốt lõi của app là **bản ghi tốt**, không phải tóm tắt.

App là web app chạy trên máy cá nhân (macOS, Apple M5, RAM 16 GB), chỉ một người dùng.

## 2. Mục tiêu

1. Upload một file ghi âm cuộc họp (kích thước tới khoảng 1 GB, ví dụ file đầu tiên khoảng 189 MB) qua giao diện web.
2. App tự chuyển giọng nói thành văn bản **ngay trên máy**, có mốc thời gian.
3. ~~Tách theo người nói~~: đã bỏ theo quyết định của người dùng (§6).
4. Xem danh sách cuộc họp; mở từng cuộc để đọc bản ghi; copy hoặc tải về dạng Markdown.
5. Mỗi cuộc họp được lưu thành **file văn bản thường trên đĩa**, để người dùng mở Claude Code tại thư mục đó và hỏi trực tiếp (kể cả hỏi xuyên nhiều cuộc họp).

## 3. Không làm (non-goals)

- **Không** tự tóm tắt bằng AI, không gọi Claude Code hay bất kỳ LLM nào. Người dùng đã nói rõ: chỉ cần ra văn bản là xong.
- Không có ô chat, hỏi đáp hay tìm kiếm trong app.
- Không nhiều người dùng, không đăng nhập, không deploy lên server.
- Không ghi âm trực tiếp trong app (chỉ upload file có sẵn).
- **Không tách người nói** (không phân biệt ai nói câu nào, không đổi tên người nói).
- **Không sửa chữ trong bản ghi** ở bản đầu (người dùng đã đồng ý). README ghi rõ: không sửa tay `transcript.md`, vì file này được sinh lại khi đổi tiêu đề hoặc ngày. Nếu dùng thực tế thấy cùng một từ bị nghe sai lặp lại nhiều, bản sau sẽ thêm "tìm và thay thế" (lưu vào `segments.json`).

## 4. Anti-goals (điều khiến app thất bại dù chạy được)

- Gửi audio hoặc bản ghi ra dịch vụ bên ngoài. Toàn bộ xử lý phải ở trên máy.
- Bản ghi bị "làm đẹp", tóm lược hay bỏ đoạn. Phải ghi đủ những gì được nói, kể cả khi có lỗi nhận dạng.
- Dữ liệu bị khóa trong định dạng riêng của app. File `transcript.md` phải đọc được bằng mắt và bằng Claude Code mà không cần app.

## 5. Ràng buộc

- macOS Apple Silicon (M5), RAM 16 GB, ổ đĩa còn trống nhiều. Có sẵn `ffmpeg` (Homebrew), Python 3.12, `uv`.
- Ngôn ngữ chính trong họp: **tiếng Việt**, chen nhiều thuật ngữ kỹ thuật tiếng Anh (LLM, RAG, GPU, Odoo, vector DB…).
- Không cần tài khoản hay token của dịch vụ ngoài để chạy (tránh các model "gated" cần đăng nhập Hugging Face). Tải model một lần lúc cài đặt là chấp nhận được.
- Code đặt ở `~/Works/meeting-transcriber/`, **tách khỏi** workspace và repo Odoo.

## 6. Những gì người dùng đã quyết

- Có web app chạy trên máy này, mở bằng trình duyệt.
- Luồng: upload → chuyển thành văn bản → xem. Không có bước tóm tắt.
- **Bỏ tách người nói**: người dùng quyết định không cần, sau khi xem kết quả chạy thật.
- Không sửa chữ trong bản ghi ở bản đầu.
- Hỏi đáp và overview: người dùng tự làm bằng Claude, bên ngoài app.

## 7. Những gì Claude tự quyết (người dùng chưa duyệt từng điểm)

- **Model chuyển giọng nói thành văn bản:** ưu tiên độ chính xác hơn tốc độ (ngưỡng chấp nhận: cuộc họp 2 tiếng xử lý xong trong khoảng 30 phút). Bước đầu của kế hoạch là **so sánh nhanh** trên khoảng 10 phút cắt từ file họp thật: Whisper `large-v3` và `large-v3-turbo` qua MLX (`mlx-whisper`), thêm PhoWhisper-large nếu chạy được trên máy. Chọn model tốt nhất làm mặc định và để model là một tùy chọn cấu hình. Ngôn ngữ cố định `vi` thay vì tự dò, vì tự dò dễ sai khi chen tiếng Anh.
- **Chống "ảo giác" của Whisper:** dùng VAD để bỏ qua đoạn im lặng hoặc nhạc, tắt `condition_on_previous_text`, bỏ những vòng lặp cùng một câu lặp liên tục. Các đoạn này không phải lời nói thật nên không vi phạm anti-goal "không bỏ nội dung".
- **Định dạng audio:** chấp nhận mọi định dạng `ffmpeg` đọc được, kể cả video (mp4…). Lúc upload, trích ra một bản audio nén (ví dụ `audio.m4a`) để xử lý và phát trên trình duyệt. File gốc chỉ giữ khi đó đã là file audio, file video thì không giữ.
- **Ô "từ khóa / thuật ngữ"** (tùy chọn) khi upload, được đưa vào làm `initial_prompt` cho Whisper (ví dụ: "Odoo, Catalog AI, gói thầu, RAG") để nhận đúng tên riêng.
- **Backend Python** (FastAPI hoặc tương đương) + giao diện HTML/JS đơn giản phục vụ ngay từ backend, không cần bước build frontend. Chạy bằng một lệnh (`./run.sh`), mở `http://localhost:8765`.
- **Xử lý nền, mỗi lần một job:** upload xong trả về ngay; trang hiện trạng thái (đang chờ / đang chuyển đổi / đang chuyển thành văn bản x% / xong / lỗi). Đóng tab rồi mở lại vẫn thấy tiến độ. Nếu server tắt giữa chừng thì job đó chuyển sang "lỗi", có nút **chạy lại**.
- **Giữ file audio gốc** trong thư mục cuộc họp và có trình phát audio trên trang chi tiết; **bấm vào mốc thời gian thì audio nhảy tới đoạn đó**, để kiểm tra những chỗ nghi nhận dạng sai.
- **Sửa tiêu đề và ngày họp** được; **xóa cuộc họp** được (có xác nhận).

## 8. Lưu trữ trên đĩa

```
~/Works/meeting-transcriber/
  meetings/
    2026-11-02-ha-tang-ai/
      audio.m4a            # audio đã trích/nén để xử lý và phát
      meeting.json         # metadata: tiêu đề, ngày, thời lượng, trạng thái
      whisper.json         # kết quả Whisper thô, trước khi lọc câu "ảo"
      segments.json        # các câu đã lọc: [{start, end, text}]
      transcript.md        # bản ghi để đọc, sinh lại từ segments.json + map tên
```

`transcript.md` có dạng:

```markdown
# Hạ tầng AI — hỏi đáp kinh nghiệm
Ngày: 2026-11-02 · Thời lượng: 1:52:10

**[00:00:05]** Hôm nay mình bàn về hạ tầng cho Catalog AI…

**[00:01:12]** Bên mình đang dùng…
```

Các câu gần nhau (cách nhau ≤ 2 giây) được gộp thành một đoạn tối đa khoảng 1 phút, mỗi đoạn mở đầu bằng mốc thời gian. Khi đổi tiêu đề/ngày thì chỉ cập nhật `meeting.json` và sinh lại `transcript.md`; `segments.json` không đổi.

Thư mục `meetings/` là thứ người dùng mở Claude Code để hỏi đáp.

## 9. Màn hình

1. **Danh sách cuộc họp:** nút upload (kéo thả file + tiêu đề + ngày + từ khóa), bảng các cuộc họp (tiêu đề, ngày, thời lượng, trạng thái/tiến độ).
2. **Chi tiết cuộc họp:** tiêu đề, trình phát audio, bản ghi theo đoạn có mốc thời gian bấm được, nút copy toàn bộ và tải `transcript.md`.

## 10. Để builder quyết

- Cấu trúc code, thư viện cụ thể cho web, cách báo tiến độ (polling hay SSE).
- Cách đặt slug thư mục cuộc họp.
- Ngưỡng gộp câu thành đoạn.
- Cách cài đặt và tải model (script setup), miễn là có hướng dẫn chạy rõ ràng trong README.

## 11. Kiểm chứng

- File họp thật đầu tiên: `~/Works/meeting-transcriber/cuoc_hop_20261102_143735.wav` (WAV PCM 16-bit, 48 kHz, mono, 34:32, 189 MB). Dùng file này cho bước so sánh model và chạy thử đầu-cuối: phải có `transcript.md` đầy đủ, mốc thời gian khớp audio.
- Đổi tiêu đề: `transcript.md` trên đĩa cập nhật đúng.
