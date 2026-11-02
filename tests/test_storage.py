import pytest

from app.storage import Meeting, MeetingStore, slugify


def make(store, title="Họp", date="2026-11-02"):
    return store.create(title=title, date=date, keywords="", diarize=True,
                        num_speakers=None, original_name="a.wav")


def test_slugify_strips_vietnamese():
    assert slugify("Hạ tầng AI — hỏi đáp", "2026-11-02") == "2026-11-02-ha-tang-ai-hoi-dap"


def test_slugify_handles_d_stroke():
    assert slugify("Đánh giá gói thầu", "2026-11-02") == "2026-11-02-danh-gia-goi-thau"


def test_slugify_empty_title():
    assert slugify("", "2026-11-02") == "2026-11-02-cuoc-hop"
    assert slugify("—!!", "2026-11-02") == "2026-11-02-cuoc-hop"


def test_slugify_truncates_long_title():
    slug = slugify("a" * 200, "2026-11-02")
    assert len(slug) <= len("2026-11-02-") + 60


def test_create_dedupes_ids(tmp_path):
    s = MeetingStore(tmp_path)
    a, b = make(s), make(s)
    assert (a.id, b.id) == ("2026-11-02-hop", "2026-11-02-hop-2")
    assert (tmp_path / a.id / "meeting.json").exists()


def test_create_sets_defaults(tmp_path):
    m = make(MeetingStore(tmp_path))
    assert m.status == "queued" and m.progress == 0.0 and m.speakers == {}
    assert m.original_name == "a.wav" and m.created_at


def test_roundtrip_and_title_edit_keeps_id(tmp_path):
    s = MeetingStore(tmp_path)
    m = make(s)
    m.title = "Khác"
    m.speakers = {"S1": "Anh Tuấn"}
    s.save(m)
    got = s.get(m.id)
    assert got.id == m.id and got.title == "Khác" and got.speakers == {"S1": "Anh Tuấn"}
    assert isinstance(got, Meeting)


def test_list_order(tmp_path):
    s = MeetingStore(tmp_path)
    old = make(s, "Cũ", "2026-10-01")
    new1 = make(s, "Mới 1", "2026-11-02")
    new2 = make(s, "Mới 2", "2026-11-02")
    assert [m.id for m in s.list()] == [new2.id, new1.id, old.id]


def test_list_skips_stray_dirs(tmp_path):
    s = MeetingStore(tmp_path)
    (tmp_path / "rac").mkdir()
    make(s)
    assert len(s.list()) == 1


def test_get_missing_raises(tmp_path):
    with pytest.raises(KeyError):
        MeetingStore(tmp_path).get("2026-11-02-khong-co")


def test_get_rejects_path_traversal(tmp_path):
    s = MeetingStore(tmp_path / "meetings")
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "meeting.json").write_text("{}")
    with pytest.raises(KeyError):
        s.get("../x")


def test_delete_removes_dir(tmp_path):
    s = MeetingStore(tmp_path)
    m = make(s)
    s.delete(m.id)
    assert not (tmp_path / m.id).exists()
    with pytest.raises(KeyError):
        s.get(m.id)


def test_json_helpers_roundtrip(tmp_path):
    s = MeetingStore(tmp_path)
    m = make(s)
    s.write_json(m.id, "segments.json", [{"text": "Xin chào"}])
    assert s.read_json(m.id, "segments.json") == [{"text": "Xin chào"}]
    assert "Xin chào" in (tmp_path / m.id / "segments.json").read_text(encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        s.read_json(m.id, "whisper.json")


def test_update_rereads_disk_so_concurrent_edits_survive(tmp_path):
    s = MeetingStore(tmp_path)
    m = make(s)
    stale = s.get(m.id)
    s.update(m.id, title="Tên mới")
    got = s.update(stale.id, status="transcribing", progress=0.5)
    assert (got.title, got.status, got.progress) == ("Tên mới", "transcribing", 0.5)
    assert s.get(m.id).title == "Tên mới"


def test_update_rejects_unknown_field(tmp_path):
    s = MeetingStore(tmp_path)
    m = make(s)
    with pytest.raises(AttributeError):
        s.update(m.id, khong_co=1)
