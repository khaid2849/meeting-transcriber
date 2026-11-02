from app.assemble import (assign_speakers, format_ts, group_paragraphs, render_markdown,
                          speaker_label)
from app.storage import Meeting


def seg(start, end, text, words=None):
    return {"start": start, "end": end, "text": text, "words": words or []}


def w(start, end, word):
    return {"start": start, "end": end, "word": word}


def utt(start, end, speaker, text):
    return {"start": start, "end": end, "speaker": speaker, "text": text}


def meeting(**kw):
    base = dict(id="2026-11-02-x", title="Hạ tầng AI", date="2026-11-02", duration=2072.25)
    base.update(kw)
    return Meeting(**base)


def test_assign_without_turns_keeps_segments():
    segs = [seg(0, 4, " Xin chào", [w(0, 1, " Xin"), w(1, 4, " chào")]), seg(5, 8, " mọi người")]
    out = assign_speakers(segs, None)
    assert [(u["speaker"], u["text"]) for u in out] == [(None, "Xin chào"), (None, "mọi người")]
    assert (out[0]["start"], out[0]["end"]) == (0, 4)


def test_assign_splits_segment_on_speaker_change():
    words = [w(0, 1, " Chào"), w(1, 2, " anh"), w(2.5, 3, " Dạ"), w(3, 4, " vâng")]
    out = assign_speakers([seg(0, 4, " Chào anh Dạ vâng", words)], [(0, 2.2, 0), (2.2, 4, 1)])
    assert [(u["speaker"], u["text"]) for u in out] == [("S1", "Chào anh"), ("S2", "Dạ vâng")]
    assert (out[1]["start"], out[1]["end"]) == (2.5, 4)


def test_word_outside_turns_takes_nearest_turn():
    out = assign_speakers([seg(10, 11, " ừ", [w(10, 11, " ừ")])], [(0, 5, 0), (6, 9.5, 1)])
    assert out[0]["speaker"] == "S2"


def test_segment_without_words_uses_max_overlap():
    out = assign_speakers([seg(0, 10, " dài")], [(0, 3, 0), (3, 10, 1)])
    assert out[0]["speaker"] == "S2"


def test_consecutive_segments_same_speaker_stay_separate_utterances():
    segs = [seg(0, 2, " a", [w(0, 2, " a")]), seg(3, 5, " b", [w(3, 5, " b")])]
    out = assign_speakers(segs, [(0, 5, 0)])
    assert [u["text"] for u in out] == ["a", "b"]


def test_speaker_label_default_and_custom():
    assert speaker_label("S2", {}) == "Người 2"
    assert speaker_label("S2", {"S2": "Anh Tuấn"}) == "Anh Tuấn"
    assert speaker_label("S2", {"S2": "  "}) == "Người 2"
    assert speaker_label(None, {}) is None


def test_group_merges_same_speaker_within_gap():
    ps = group_paragraphs([utt(0, 2, "S1", "a"), utt(3, 5, "S1", "b"), utt(5.5, 6, "S2", "c")], 2.0, 60.0)
    assert [(p["speaker"], p["text"], p["start"]) for p in ps] == [("S1", "a b", 0), ("S2", "c", 5.5)]


def test_group_breaks_on_long_gap():
    ps = group_paragraphs([utt(0, 2, "S1", "a"), utt(5, 6, "S1", "b")], 2.0, 60.0)
    assert len(ps) == 2


def test_group_splits_long_unlabeled_run():
    utts = [utt(i * 10, i * 10 + 9.5, None, f"t{i}") for i in range(10)]
    ps = group_paragraphs(utts, 2.0, 60.0)
    assert len(ps) == 2 and ps[1]["start"] == 60


def test_format_ts():
    assert format_ts(3725.9) == "01:02:05"
    assert format_ts(0) == "00:00:00"


def test_render_markdown():
    utts = [utt(5, 8, "S1", "Hôm nay mình bàn về hạ tầng"), utt(9, 12, "S2", "Bên mình đang dùng GPU")]
    md = render_markdown(meeting(speakers={"S1": "Anh Tuấn"}), utts)
    assert md.startswith(
        "# Hạ tầng AI\nNgày: 2026-11-02 · Thời lượng: 00:34:32 · Người nói: Anh Tuấn, Người 2\n")
    assert "**[00:00:05] Anh Tuấn:** Hôm nay mình bàn về hạ tầng" in md
    assert "**[00:00:09] Người 2:** Bên mình đang dùng GPU" in md


def test_render_without_speakers():
    md = render_markdown(meeting(duration=None), [utt(5, 8, None, "Xin chào")])
    assert md.splitlines()[1] == "Ngày: 2026-11-02"
    assert "**[00:00:05]** Xin chào" in md
