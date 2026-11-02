# Hỏi AI trên bản ghi — Thiết kế

Ngày: 2026-11-02 · Trạng thái: người dùng đã duyệt mô tả trong chat ("ok build đi").

## Mục đích

Sau khi có bản ghi, người dùng muốn dùng LLM để tóm tắt, hỏi đáp trên nội dung cuộc họp, ngay trong web app. AI không gắn cứng: người dùng tự cấu hình nhà cung cấp trên web.

## Người dùng đã quyết

- Nhà cung cấp: **Claude, OpenAI, Gemini**; cấu hình được **nhiều** bộ (mỗi bộ: tên, nhà cung cấp, API key, model).
- Hỏi trong **một** cuộc họp và **xuyên nhiều** cuộc họp.
- **Lưu lịch sử** hỏi đáp.
- Chấp nhận: khi dùng AI, bản ghi được gửi tới nhà cung cấp đã chọn (thay đổi so với spec gốc, chỉ khi người dùng chủ động hỏi).

## Thiết kế (Claude đề xuất, người dùng đồng ý qua playback)

- **Cuộc trò chuyện** = một danh sách cuộc họp (phạm vi) + các tin nhắn. Lưu ở `data/chats/<id>.json`. Thanh bên có tab **Cuộc họp | Hỏi AI**; trang cuộc họp có nút "Hỏi AI" tạo cuộc trò chuyện gắn sẵn cuộc họp đó.
- **Ngữ cảnh**: gửi nguyên văn toàn bộ bản ghi các cuộc họp trong phạm vi (không RAG). Mỗi cuộc họp có nhãn `M1`, `M2`…; AI dẫn nguồn dạng `[M1 00:11:41]`, giao diện biến thành link mở đúng đoạn audio. Hiện ước lượng số token trước khi gửi.
- **Trả lời**: tiếng Việt, Markdown, chỉ dựa trên bản ghi, nói rõ khi không có thông tin. Stream từng phần.
- **Nút gợi ý**: Tóm tắt, Các quyết định, Việc cần làm.
- **Cài đặt**: `data/ai_settings.json` (quyền 600). API không bao giờ trả key về trình duyệt, chỉ trả gợi ý dạng `sk-…a1b2`. Danh sách model lấy trực tiếp từ nhà cung cấp; vẫn gõ tay được.

## Claude tự quyết

- SDK chính thức của từng hãng (`anthropic`, `openai`, `google-genai`). OpenAI dùng Chat Completions (stream).
- Claude: bật prompt caching cho phần bản ghi (hỏi tiếp rẻ hơn); `max_tokens` 64000 khi stream; với `claude-fable-5-1`, `claude-opus-5-5`, `claude-opus-5`, `claude-sonnet-5-5` bật refusal fallback phía server (`fallbacks: "default"`).
- Tin nhắn người dùng được lưu ngay; câu trả lời lưu khi stream xong. Lỗi giữa chừng: lưu phần đã nhận kèm cờ lỗi.
- Cuộc họp bị xóa hoặc chưa xử lý xong thì bỏ qua khỏi ngữ cảnh và hiện ghi chú.
- Markdown hiển thị bằng `marked` + `DOMPurify` (đóng gói sẵn trong `app/static/vendor/`, không tải từ mạng lúc chạy).

## Không làm

- Không RAG / embedding, không tìm kiếm.
- Không đăng nhập (app chỉ trong tailnet của người dùng).
- Không tool use / agent.
