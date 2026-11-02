# So sánh model nhận dạng giọng nói

Ngày: 2026-11-02. Mẫu: 10 phút (phút 10–20) của một cuộc họp thật, tiếng Việt chen nhiều thuật ngữ kỹ thuật tiếng Anh (GPU, inference, training…). WAV mono 48 kHz, ghi âm trong phòng. Máy: Apple M5, 16 GB RAM. Không dùng VAD, `language="vi"`, `condition_on_previous_text=False`.

Repo public nên tài liệu này chỉ ghi nhận xét, không trích nội dung cuộc họp.

| Model | Thời gian cho 10 phút audio | Ước tính cho họp 2 tiếng | Nhận xét |
|---|---|---|---|
| `mlx-community/whisper-large-v3-mlx` | 105 s | ~21 phút | Thuật ngữ tiếng Anh ra đúng phần lớn (inference, training, card, mã GPU). Câu tiếng Việt mạch lạc, có dấu câu. |
| `mlx-community/whisper-large-v3-turbo` | 28 s | ~6 phút | Nhanh gấp ~4 lần nhưng sai thuật ngữ nhiều hơn rõ rệt: phiên âm thuật ngữ tiếng Anh thành từ tiếng Việt gần âm, nghe nhầm tên sản phẩm, đôi chỗ ra câu vô nghĩa. |
| `vinai/PhoWhisper-large` | — | — | Chưa có kết quả: chỉ có trọng số PyTorch 6,2 GB, tải lâu. Sẽ bổ sung. |

**Chọn: `whisper-large-v3-mlx`.** Giá trị của app nằm ở bản ghi chính xác (spec §1), và thời gian vẫn nằm trong ngưỡng 30 phút cho cuộc họp 2 tiếng (spec §7). Đổi model: sửa `WHISPER_MODEL` trong `app/config.py`.

## Ghi nhận khi chạy thật

- Whisper tiếng Việt hay sinh câu kết video kiểu "Cảm ơn các bạn đã theo dõi…" ở rìa đoạn audio, dài 0,1 giây cho cả câu. App bỏ các đoạn nói nhanh hơn 10 từ/giây (từ 5 từ trở lên) và các câu kết video đã biết (`app/filters.py`).
