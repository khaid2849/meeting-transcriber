"""Build the system prompt that carries meeting transcripts to the LLM."""
from html import escape

from app.assemble import format_ts

INSTRUCTIONS = """Bạn là trợ lý giúp người dùng nắm nội dung các cuộc họp nội bộ của họ.

- Trả lời bằng tiếng Việt, rõ ràng, có cấu trúc (Markdown: tiêu đề ngắn, gạch đầu dòng khi hợp lý).
- Chỉ dựa trên các bản ghi bên dưới. Nếu bản ghi không có thông tin để trả lời, hãy nói rõ là không có.
- Bản ghi được tạo tự động từ giọng nói nên có thể có từ nghe sai hoặc câu vô nghĩa; hãy hiểu theo ngữ cảnh và nói rõ khi không chắc.
- Khi nêu một ý lấy từ bản ghi, ghi nguồn ngay sau ý đó theo đúng dạng [M1 00:11:41], trong đó M1 là mã cuộc họp và 00:11:41 là mốc thời gian của đoạn bạn dựa vào."""


def build_system_prompt(meetings: list[dict]) -> tuple[str, dict[str, str]]:
    """meetings: [{id, title, date, paragraphs: [{start, text}]}]. Returns (prompt, {label: meeting_id})."""
    labels: dict[str, str] = {}
    parts = [INSTRUCTIONS, ""]
    if not meetings:
        parts.append("Không có bản ghi nào được chọn cho cuộc trò chuyện này.")
    for i, m in enumerate(meetings, start=1):
        label = f"M{i}"
        labels[label] = m["id"]
        lines = [f"[{format_ts(p['start'])}] {p['text']}" for p in m["paragraphs"]]
        parts.append(f'<meeting id="{label}" title="{escape(m["title"])}" date="{escape(m["date"])}">')
        parts.extend(lines)
        parts.append("</meeting>")
    return "\n".join(parts), labels


def estimate_tokens(text: str) -> int:
    """Rough count: Vietnamese text averages about three characters per token."""
    return len(text) // 3
