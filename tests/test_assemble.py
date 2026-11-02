from app.assemble import format_ts, group_paragraphs, render_markdown, to_utterances
from app.storage import Meeting


def seg(start, end, text):
    return {"start": start, "end": end, "text": text, "words": []}


def utt(start, end, text):
    return {"start": start, "end": end, "text": text}


def meeting(**kw):
    base = dict(id="2026-11-02-x", title="Hạ tầng AI", date="2026-11-02", duration=2072.25)
    base.update(kw)
    return Meeting(**base)


def test_to_utterances_strips_text_and_drops_empty():
    out = to_utterances([seg(0, 4, " Xin chào "), seg(4, 5, "  "), seg(5, 8, " mọi người")])
    assert out == [utt(0, 4, "Xin chào"), utt(5, 8, "mọi người")]


def test_group_merges_within_gap():
    ps = group_paragraphs([utt(0, 2, "a"), utt(3, 5, "b")], 2.0, 60.0)
    assert [(p["text"], p["start"], p["end"]) for p in ps] == [("a b", 0, 5)]


def test_group_breaks_on_long_gap():
    assert len(group_paragraphs([utt(0, 2, "a"), utt(5, 6, "b")], 2.0, 60.0)) == 2


def test_group_splits_long_run():
    utts = [utt(i * 10, i * 10 + 9.5, f"t{i}") for i in range(10)]
    ps = group_paragraphs(utts, 2.0, 60.0)
    assert len(ps) == 2 and ps[1]["start"] == 60


def test_group_ignores_legacy_speaker_keys():
    legacy = [{"start": 0, "end": 2, "speaker": "S1", "text": "a"},
              {"start": 2.5, "end": 4, "speaker": "S2", "text": "b"}]
    assert [p["text"] for p in group_paragraphs(legacy, 2.0, 60.0)] == ["a b"]


def test_format_ts():
    assert format_ts(3725.9) == "01:02:05"
    assert format_ts(0) == "00:00:00"


def test_render_markdown():
    md = render_markdown(meeting(), [utt(5, 8, "Hôm nay mình bàn về hạ tầng"), utt(30, 32, "Bên mình dùng GPU")])
    assert md.startswith("# Hạ tầng AI\nNgày: 2026-11-02 · Thời lượng: 00:34:32\n")
    assert "**[00:00:05]** Hôm nay mình bàn về hạ tầng" in md
    assert "**[00:00:30]** Bên mình dùng GPU" in md
    assert "Người" not in md


def test_render_without_duration():
    md = render_markdown(meeting(duration=None), [utt(5, 8, "Xin chào")])
    assert md.splitlines()[1] == "Ngày: 2026-11-02"
