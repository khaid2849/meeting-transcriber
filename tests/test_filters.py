import pytest

from app.filters import clean_segments, speech_chunks


def s(text, **kw):
    words = [{"start": i, "end": i + 1, "word": " " + t} for i, t in enumerate(text.split())]
    end = max(1.0, len(text.split()) * 0.3)
    return {"start": 0.0, "end": end, "text": " " + text, "words": words, **kw}


def approx(chunks):
    return [(round(a, 3), round(b, 3)) for a, b in chunks]


def test_chunks_merge_close_regions_up_to_max_len():
    assert approx(speech_chunks([(0, 5), (5.5, 10), (10.3, 30)], max_len=28)) == [(0, 10.2), (10.1, 30.2)]


def test_chunk_splits_region_longer_than_max_len():
    assert speech_chunks([(0, 60)], max_len=28, pad=0) == [(0, 28), (28, 56), (56, 60)]


def test_chunks_clamped_to_total():
    assert approx(speech_chunks([(0.1, 9.95)], max_len=28, total=10.0)) == [(0, 10.0)]


def test_chunks_empty():
    assert speech_chunks([], max_len=28) == []


def test_clean_drops_known_outro_only_segments():
    segs = [s("Hãy subscribe cho kênh Ghiền Mì Gõ để không bỏ lỡ"), s("Chúng ta bàn về GPU")]
    assert [x["text"] for x in clean_segments(segs)] == [" Chúng ta bàn về GPU"]


def test_clean_keeps_long_segment_mentioning_subscribe():
    text = "khách hàng phải subscribe gói dịch vụ " + "rồi mới dùng được tính năng này " * 4
    assert len(clean_segments([s(text)])) == 1


def test_clean_collapses_repeated_segments():
    segs = [s("Cảm ơn."), s("Cảm ơn."), s("Cảm ơn."), s("Tiếp theo")]
    assert [x["text"] for x in clean_segments(segs)] == [" Cảm ơn.", " Tiếp theo"]


def test_clean_keeps_two_legit_repeats():
    segs = [s("Đúng rồi."), s("Đúng rồi.")]
    assert len(clean_segments(segs)) == 2


def test_clean_collapses_phrase_loop_inside_segment():
    out = clean_segments([s("ok ok và và và và và và")])[0]
    assert out["text"] == " ok ok và"
    assert [x["word"] for x in out["words"]] == [" ok", " ok", " và"]


def test_clean_collapses_multiword_loop():
    out = clean_segments([s("chúng ta " * 5 + "đi")])[0]
    assert out["text"] == " chúng ta đi"


@pytest.mark.parametrize("nsp,logp,kept", [(0.8, -1.2, False), (0.8, -0.3, True), (0.2, -1.2, True)])
def test_clean_drops_low_confidence_no_speech(nsp, logp, kept):
    assert (len(clean_segments([s("ừm", no_speech_prob=nsp, avg_logprob=logp)])) == 1) is kept


def test_clean_drops_impossibly_fast_segment():
    fast = s("Mình sẽ test trên bộ dữ liệu ba mươi hồ sơ")
    fast["start"], fast["end"] = 55.4, 55.5
    ok = s("Mình sẽ test trên bộ dữ liệu")
    ok["start"], ok["end"] = 50.0, 53.0
    assert clean_segments([ok, fast]) == [ok]


def test_clean_keeps_short_fast_interjection():
    seg = s("dạ vâng")
    seg["start"], seg["end"] = 10.0, 10.1
    assert len(clean_segments([seg])) == 1


def test_clean_drops_thanks_for_watching_outro():
    assert clean_segments([s("Cảm ơn các bạn đã theo dõi và hẹn gặp lại.")]) == []
