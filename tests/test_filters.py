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


@pytest.mark.parametrize("text", ["Mình cần subscribe follower vào record đó",
                                  "Kafka consumer subscribe vào topic này"])
def test_clean_keeps_technical_subscribe(text):
    assert len(clean_segments([s(text)])) == 1


def test_clean_keeps_fast_real_speech():
    seg = s("dạ vâng em hiểu rồi ạ")
    seg["start"], seg["end"] = 10.0, 10.55
    assert len(clean_segments([seg])) == 1


def test_clean_reports_drops():
    dropped = []
    clean_segments([s("Hãy subscribe cho kênh Ghiền Mì Gõ"), s("Chúng ta bàn về GPU")],
                   on_drop=lambda seg, reason: dropped.append(reason))
    assert dropped == ["outro"]


def timed(start, end, text):
    return {"start": start, "end": end, "text": " " + text, "words": []}


def test_needs_retry_when_a_hallucination_was_dropped():
    from app.filters import needs_retry
    segs = [timed(0, 3, "Nếu bạn muốn tham gia thông tin, hãy đăng ký kênh để nhận thông tin nhất.")]
    assert needs_retry(segs, speech_seconds=3.0)


def test_needs_retry_when_text_is_too_sparse_for_the_speech():
    from app.filters import needs_retry
    assert needs_retry([timed(0, 2, "vâng ạ")], speech_seconds=16.0)


def test_no_retry_for_normal_output():
    from app.filters import needs_retry
    segs = [timed(0, 4, "những cái này thì đó cũng là một cái rất là quan trọng"),
            timed(4, 8, "thì thường anh em mình sẽ đi thuê ngoài để dựng")]
    assert not needs_retry(segs, speech_seconds=8.0)
    assert not needs_retry([], speech_seconds=0.3)


def test_pick_richer_prefers_more_clean_text():
    from app.filters import pick_richer
    halluc = [timed(0, 3, "Cảm ơn các bạn đã theo dõi và hẹn gặp lại.")]
    real = [timed(0, 3, "ngoài cái em mình còn những cái thằng khác nữa")]
    assert pick_richer(halluc, real) is real
    assert pick_richer(real, halluc) is real


@pytest.mark.parametrize("text", ["Thanks for watching!", "Thank you for watching.",
                                  "Please like and subscribe", "Subscribe to my channel for more videos"])
def test_clean_drops_english_outros(text):
    assert clean_segments([s(text)]) == []


def test_clean_keeps_english_technical_subscribe():
    assert len(clean_segments([s("Each consumer will subscribe to the topic and read new events")])) == 1
