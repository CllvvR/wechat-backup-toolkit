"""Opt-in, lossless incomplete media-reference export tests (synthetic data only)."""

import sqlite3
from types import SimpleNamespace

import pytest

from wechat_backup_converter import export as export_module
from wechat_backup_converter.cli import build_parser
from wechat_backup_converter.export import export_text, initialize_output
from wechat_backup_converter.index import TextSegment
from wechat_backup_converter.proto import ParsedMessage


def _make_message(media_paths):
    return ParsedMessage(
        msg_type=3,
        field2_text=None,
        from_username=None,
        to_username=None,
        content="synthetic message",
        status=0,
        create_time=1,
        field8_text=None,
        legacy_server_id=0,
        media_count=len(media_paths),
        media_paths=media_paths,
        media_types=list(range(1, len(media_paths) + 1)),
        msg_server_id=1,
        msg_seq=0,
        create_time_ms=0,
        flag=0,
    )


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    db = sqlite3.connect(":memory:")
    initialize_output(db)
    text = tmp_path / "BAK_0_TEXT"
    text.write_bytes(b"fake encrypted data")
    segment = TextSegment(
        row_id=1, talker_id=1, start_time=1, end_time=2, offset=0,
        length=16, username="synthetic", status=0, reserved1=0,
        file_path="", segment_id="segment", reserved2="", reserved3="",
    )
    index = SimpleNamespace(
        text_segments=[segment], by_identifier={"known-id": 7},
    )
    monkeypatch.setattr(export_module, "_decrypt_text_segment", lambda *_: b"segment")
    monkeypatch.setattr(export_module, "parse_segment", lambda *_: (1, [b"message"]))
    try:
        yield db, text, index, monkeypatch
    finally:
        db.close()


def test_strict_export_refuses_missing_media(prepared):
    db, text, index, monkeypatch = prepared
    monkeypatch.setattr(
        export_module, "parse_message", lambda *_: _make_message(["missing-id"])
    )
    with pytest.raises(ValueError, match="unknown media identifier"):
        export_text(db, text, b"0123456789abcdef", index)


def test_opt_in_export_preserves_known_and_missing_references(prepared):
    db, text, index, monkeypatch = prepared
    monkeypatch.setattr(
        export_module, "parse_message",
        lambda *_: _make_message(["known-id", "missing-id"]),
    )
    count, media_types = export_text(
        db, text, b"0123456789abcdef", index, allow_missing_media=True,
    )
    assert count == 1
    assert media_types == {7: {1}}
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert db.execute("SELECT message_id,ordinal,media_id,media_type,identifier "
                      "FROM message_media").fetchall() == [(1, 0, 7, 1, "known-id")]
    assert db.execute(
        "SELECT message_id,ordinal,media_type,identifier,reason "
        "FROM unresolved_message_media"
    ).fetchall() == [(1, 1, 2, "missing-id", "missing_from_backup_index")]
    assert '"missing-id"' in db.execute(
        "SELECT media_paths_json FROM messages"
    ).fetchone()[0]


def test_opt_in_export_preserves_null_reference(prepared):
    db, text, index, monkeypatch = prepared
    monkeypatch.setattr(
        export_module, "parse_message", lambda *_: _make_message([None])
    )
    count, media_types = export_text(
        db, text, b"0123456789abcdef", index, allow_missing_media=True,
    )
    assert count == 1
    assert media_types == {}
    assert db.execute(
        "SELECT identifier FROM unresolved_message_media"
    ).fetchone()[0] is None


def test_cli_missing_media_flag_is_explicit_opt_in():
    parser = build_parser()
    opts = parser.parse_args(["convert", "--input", "synthetic"])
    assert opts.allow_missing_media is False
    opted = parser.parse_args(
        ["convert", "--input", "synthetic", "--allow-missing-media"]
    )
    assert opted.allow_missing_media is True
