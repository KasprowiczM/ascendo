"""ChatsDB: SQLite persistence for chat conversations.

Mirror of InventoryDB pattern from Sesja 67. Schema v1; WAL mode;
per-call connections (thread-safe via check_same_thread=False).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    backend       TEXT NOT NULL,
    model         TEXT,
    locale        TEXT NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0,
    pinned        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    template_id     TEXT,
    context_tags    TEXT,
    actions         TEXT,
    action_clicked  TEXT,
    action_result   TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at      ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_conversations_archived   ON conversations(archived);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChatsDB:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        if os.name == "posix":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate(self) -> None:
        conn = self._connect()
        try:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            if ver == 0:
                conn.executescript(SCHEMA_V1)
                conn.execute("PRAGMA user_version = 1")
                conn.commit()
        finally:
            conn.close()

    # -------- conversations --------

    def create_conversation(
        self, *, backend: str, locale: str, model: str | None = None,
    ) -> str:
        cid = str(uuid.uuid4())
        now = _now()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO conversations "
                "(id, title, created_at, updated_at, backend, model, locale) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, "Untitled", now, now, backend, model, locale),
            )
            conn.commit()
        finally:
            conn.close()
        return cid

    def list_conversations(
        self, *, archived: bool = False, query: str | None = None,
    ) -> list[dict]:
        conn = self._connect()
        try:
            args: list[Any]
            if query:
                sql = (
                    "SELECT DISTINCT c.* FROM conversations c "
                    "LEFT JOIN messages m ON m.conversation_id = c.id "
                    "WHERE c.archived = ? AND (c.title LIKE ? OR m.content LIKE ?) "
                    "ORDER BY c.pinned DESC, c.updated_at DESC"
                )
                like = f"%{query}%"
                args = [1 if archived else 0, like, like]
            else:
                sql = (
                    "SELECT * FROM conversations WHERE archived = ? "
                    "ORDER BY pinned DESC, updated_at DESC"
                )
                args = [1 if archived else 0]
            rows = conn.execute(sql, args).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_conversation(
        self,
        cid: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
        pinned: bool | None = None,
    ) -> None:
        sets: list[str] = []
        args: list[Any] = []
        if title is not None:
            sets.append("title = ?"); args.append(title)
        if archived is not None:
            sets.append("archived = ?"); args.append(1 if archived else 0)
        if pinned is not None:
            sets.append("pinned = ?"); args.append(1 if pinned else 0)
        if not sets:
            return
        sets.append("updated_at = ?"); args.append(_now())
        args.append(cid)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", args)
            conn.commit()
        finally:
            conn.close()

    def delete_conversation(self, cid: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
            conn.commit()
        finally:
            conn.close()

    # -------- messages --------

    def append_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        template_id: str | None = None,
        context_tags: list[str] | None = None,
        actions: list[dict] | None = None,
        action_clicked: str | None = None,
        action_result: dict | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO messages "
                "(conversation_id, role, content, template_id, context_tags, "
                " actions, action_clicked, action_result, tokens_in, tokens_out, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id, role, content, template_id,
                    json.dumps(context_tags) if context_tags else None,
                    json.dumps(actions) if actions else None,
                    action_clicked,
                    json.dumps(action_result) if action_result else None,
                    tokens_in, tokens_out, now,
                ),
            )
            # Auto-title: first user message becomes the title if title is "Untitled".
            if role == "user":
                row = conn.execute(
                    "SELECT title FROM conversations WHERE id = ?", (conversation_id,),
                ).fetchone()
                if row and row["title"] == "Untitled":
                    title = content.strip().split("\n")[0][:60]
                    conn.execute(
                        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                        (title, now, conversation_id),
                    )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_messages(self, conversation_id: str) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
