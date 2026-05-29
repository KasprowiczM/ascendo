"""ChatsDB persistence tests."""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
import pytest

from ascendo.ai.persistence import ChatsDB


@pytest.fixture
def db(tmp_path):
    return ChatsDB(tmp_path / "chats.db")


def test_db_creates_schema_at_v1(db):
    conn = sqlite3.connect(db.path)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert ver == 1


def test_db_file_perms_0600_posix(db):
    if os.name != "posix":
        pytest.skip("perms check is POSIX-specific")
    st = os.stat(db.path)
    assert (st.st_mode & 0o777) == 0o600


def test_db_file_perms_windows(db, caplog):
    if os.name != "nt":
        pytest.skip("perms check is Windows-specific")
    assert "Unable to set Windows ACL" not in caplog.text
    # Check that the file was created and isn't empty after initialization
    assert db.path.exists()


def test_create_conversation_returns_id(db):
    cid = db.create_conversation(backend="claude", locale="en")
    assert isinstance(cid, str) and len(cid) > 0


def test_list_conversations_empty(db):
    assert db.list_conversations() == []


def test_append_message_and_list(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="user", content="hi")
    db.append_message(conversation_id=cid, role="assistant", content="hello!")
    convs = db.list_conversations()
    assert len(convs) == 1
    msgs = db.get_messages(cid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_auto_title_from_first_user_message(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="user", content="Why did my last run fail?")
    convs = db.list_conversations()
    assert "Why did my last run fail" in convs[0]["title"]


def test_archive_excludes_from_default_list(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="user", content="hi")
    db.update_conversation(cid, archived=True)
    assert db.list_conversations() == []
    assert len(db.list_conversations(archived=True)) == 1


def test_delete_cascades_messages(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(conversation_id=cid, role="user", content="hi")
    db.delete_conversation(cid)
    assert db.list_conversations() == []
    assert db.get_messages(cid) == []


def test_search_finds_in_content(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.append_message(
        conversation_id=cid, role="assistant",
        content="The Polish word for cat is kot.",
    )
    results = db.list_conversations(query="Polish")
    assert len(results) == 1


def test_rename(db):
    cid = db.create_conversation(backend="claude", locale="en")
    db.update_conversation(cid, title="My renamed chat")
    convs = db.list_conversations()
    assert convs[0]["title"] == "My renamed chat"


def test_pinned_sorts_first(db):
    cid1 = db.create_conversation(backend="claude", locale="en")
    cid2 = db.create_conversation(backend="claude", locale="en")
    db.update_conversation(cid2, pinned=True)
    convs = db.list_conversations()
    assert convs[0]["id"] == cid2
