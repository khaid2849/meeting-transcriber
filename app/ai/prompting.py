"""Build the system prompt that carries transcripts to the LLM."""
from html import escape

from app.assemble import format_ts

INSTRUCTIONS = {
    "vi": """Bạn là trợ lý giúp người dùng nắm nội dung các bản ghi âm thanh và video của họ (buổi trao đổi, bài giảng, phỏng vấn, video…).

- Trả lời bằng tiếng Việt, rõ ràng, có cấu trúc (Markdown: tiêu đề ngắn, gạch đầu dòng khi hợp lý).
- Chỉ dựa trên văn bản chép lời bên dưới. Nếu không có thông tin để trả lời, hãy nói rõ là không có.
- Văn bản được chép tự động từ giọng nói nên có thể có từ nghe sai hoặc câu vô nghĩa; hãy hiểu theo ngữ cảnh và nói rõ khi không chắc.
- Khi nêu một ý lấy từ văn bản, ghi nguồn ngay sau ý đó theo đúng dạng [M1 00:11:41], trong đó M1 là mã bản ghi và 00:11:41 là mốc thời gian của đoạn bạn dựa vào.""",
    "en": """You help the user understand the content of their audio and video recordings (discussions, lectures, interviews, videos…).

- Answer in English, clearly and with structure (Markdown: short headings, bullet points where useful).
- Base your answer only on the transcripts below, which may be in another language. If they do not contain the answer, say so.
- The transcripts are produced automatically from speech and may contain misheard words or nonsense; interpret them from context and say when you are unsure.
- When you state something taken from a transcript, cite it right after in exactly this form [M1 00:11:41], where M1 is the recording id and 00:11:41 the timestamp of the passage you relied on.""",
}
NO_RECORDINGS = {"vi": "Không có bản ghi nào được chọn cho cuộc trò chuyện này.",
                 "en": "No recordings were selected for this conversation."}


def build_system_prompt(meetings: list[dict], lang: str = "vi") -> tuple[str, dict[str, str]]:
    """meetings: [{id, title, date, paragraphs: [{start, text}]}]. Returns (prompt, {label: meeting_id})."""
    lang = lang if lang in INSTRUCTIONS else "vi"
    labels: dict[str, str] = {}
    parts = [INSTRUCTIONS[lang], ""]
    if not meetings:
        parts.append(NO_RECORDINGS[lang])
    for i, m in enumerate(meetings, start=1):
        label = f"M{i}"
        labels[label] = m["id"]
        parts.append(f'<recording id="{label}" title="{escape(m["title"])}" date="{escape(m["date"])}">')
        parts.extend(f"[{format_ts(p['start'])}] {p['text']}" for p in m["paragraphs"])
        parts.append("</recording>")
    return "\n".join(parts), labels


def estimate_tokens(text: str) -> int:
    """Rough count: Vietnamese text averages about three characters per token."""
    return len(text) // 3
