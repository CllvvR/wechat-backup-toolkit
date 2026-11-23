"""Synthetic sparse-media index regression tests.

No actual WeChat accounts, messages, media files, keys, or chat data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from wechat_backup_converter.archive import BackupPaths
from wechat_backup_converter.index import _load_media


def _make_index(tmp_path: Path, *, second_offset: int, size: int):
    media = tmp_path / "BAK_0_MEDIA"
    media.write_bytes(bytes([0xA5]) * size)
    paths = BackupPaths(tmp_path, tmp_path / "Backup.db", tmp_path / "BAK_0_TEXT",
                        {media.name: media})
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE MsgMedia(
            MediaId INTEGER, talkerId INTEGER, MsgSegmentId INTEGER,
            SvrId INTEGER, MD5 TEXT, talker TEXT, MediaIdStr TEXT
        );
        CREATE TABLE MsgFileSegment(
            MapKey TEXT, InnerOffSet INTEGER, Length INTEGER,
            TotalLen INTEGER, OffSet INTEGER, FileName TEXT
        );
        CREATE TABLE MediaStr2ID(UsrName TEXT);
        INSERT INTO MsgMedia VALUES(1,1,1,1,'','synthetic','media-one');
        INSERT INTO MsgMedia VALUES(2,1,1,2,'','synthetic','media-two');
        INSERT INTO MediaStr2ID VALUES('media-one');
        INSERT INTO MediaStr2ID VALUES('media-two');
        INSERT INTO MsgFileSegment VALUES('1',0,16,16,0,'BAK_0_MEDIA');
    """)
    db.execute(
        "INSERT INTO MsgFileSegment VALUES (?,?,?,?,?,?)",
        ("2", 0, 16, 16, second_offset, "BAK_0_MEDIA"),
    )
    db.commit()
    return db, paths


def test_sparse_media_index_keeps_referenced_ranges(tmp_path):
    db, paths = _make_index(tmp_path, second_offset=48, size=64)
    try:
        media, ordered, identifiers = _load_media(db, paths, require_media=True)
    finally:
        db.close()
    assert len(media) == len(ordered) == len(identifiers) == 2
    assert [obj.first_offset for obj in ordered] == [0, 48]
    assert sum(obj.encrypted_size for obj in ordered) == 32


def test_sparse_media_index_rejects_overlap(tmp_path):
    db, paths = _make_index(tmp_path, second_offset=0, size=16)
    try:
        with pytest.raises(ValueError, match="overlapping media segment offsets"):
            _load_media(db, paths, require_media=True)
    finally:
        db.close()


def test_sparse_media_index_rejects_trailing_unindexed_bytes(tmp_path):
    db, paths = _make_index(tmp_path, second_offset=48, size=80)
    try:
        with pytest.raises(ValueError, match="index covers"):
            _load_media(db, paths, require_media=True)
    finally:
        db.close()


def test_sparse_media_index_rejects_missing_media_container(tmp_path):
    db, paths = _make_index(tmp_path, second_offset=48, size=64)
    paths.media.clear()
    try:
        with pytest.raises(FileNotFoundError, match="missing media container"):
            _load_media(db, paths, require_media=True)
    finally:
        db.close()
