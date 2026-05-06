import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from google.genai import types

from config import conf

db_path: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db_path() -> str:
    if db_path is None:
        raise RuntimeError("Database is not initialized")
    return db_path


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: str) -> None:
    global db_path
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    db_path = path
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id INTEGER PRIMARY KEY,
                model TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'model')),
                content TEXT NOT NULL,
                model TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_messages_user_id_id
            ON chat_messages(user_id, id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        access_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(access_requests)").fetchall()
        }
        if access_columns and "subject_type" not in access_columns:
            conn.execute("DROP TABLE access_requests")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS access_requests (
                subject_type TEXT NOT NULL CHECK(subject_type IN ('user', 'chat')),
                subject_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected', 'revoked')),
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                chat_title TEXT,
                requested_by INTEGER,
                requested_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER,
                PRIMARY KEY (subject_type, subject_id)
            )
            """
        )


def get_user_model(user_id: int) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT model FROM user_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return row[0]


def set_user_model(user_id: int, model: str) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_sessions (user_id, model, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (user_id, model, now, now),
        )


def load_history(user_id: int, limit_turns: int | None = None) -> list[types.Content]:
    if limit_turns is None:
        limit_turns = conf["max_history_turns"]
    limit = limit_turns * 2
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content
            FROM (
                SELECT id, role, content
                FROM chat_messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (user_id, limit),
        ).fetchall()

    return [
        types.Content(
            role=role,
            parts=[types.Part.from_text(text=content)],
        )
        for role, content in rows
    ]


def append_turn(
    user_id: int,
    model: str,
    user_text: str,
    model_text: str,
    max_turns: int | None = None,
) -> None:
    if max_turns is None:
        max_turns = conf["max_history_turns"]
    now = _now()
    keep_count = max_turns * 2
    with _connect() as conn:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO user_sessions (user_id, model, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (user_id, model, now, now),
        )
        conn.execute(
            """
            INSERT INTO chat_messages (user_id, role, content, model, created_at)
            VALUES (?, 'user', ?, ?, ?)
            """,
            (user_id, user_text, model, now),
        )
        conn.execute(
            """
            INSERT INTO chat_messages (user_id, role, content, model, created_at)
            VALUES (?, 'model', ?, ?, ?)
            """,
            (user_id, model_text, model, now),
        )
        conn.execute(
            """
            DELETE FROM chat_messages
            WHERE user_id = ?
              AND id NOT IN (
                  SELECT id
                  FROM chat_messages
                  WHERE user_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (user_id, user_id, keep_count),
        )


def clear_user_history(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))


def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM bot_settings WHERE key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return default
    return row[0]


def set_setting(key: str, value: str) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO bot_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, now),
        )


def get_access_requests_enabled() -> bool:
    return get_setting("access_requests_enabled", "1") == "1"


def set_access_requests_enabled(enabled: bool) -> None:
    set_setting("access_requests_enabled", "1" if enabled else "0")


def get_access_status(subject_type: str, subject_id: int) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT status
            FROM access_requests
            WHERE subject_type = ? AND subject_id = ?
            """,
            (subject_type, subject_id),
        ).fetchone()
    if row is None:
        return None
    return row[0]


def create_access_request(
    subject_type: str,
    subject_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    chat_title: str | None,
    requested_by: int,
) -> tuple[str, bool]:
    if subject_type not in {"user", "chat"}:
        raise ValueError("Access subject type must be user or chat")

    now = _now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT status
            FROM access_requests
            WHERE subject_type = ? AND subject_id = ?
            """,
            (subject_type, subject_id),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO access_requests (
                    subject_type,
                    subject_id,
                    status,
                    username,
                    first_name,
                    last_name,
                    chat_title,
                    requested_by,
                    requested_at
                )
                VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject_type,
                    subject_id,
                    username,
                    first_name,
                    last_name,
                    chat_title,
                    requested_by,
                    now,
                ),
            )
            return "pending", True

        status = row[0]
        if status == "pending":
            conn.execute(
                """
                UPDATE access_requests
                SET username = ?,
                    first_name = ?,
                    last_name = ?,
                    chat_title = ?,
                    requested_by = ?
                WHERE subject_type = ? AND subject_id = ?
                """,
                (
                    username,
                    first_name,
                    last_name,
                    chat_title,
                    requested_by,
                    subject_type,
                    subject_id,
                ),
            )
        return status, False


def review_access_request(
    subject_type: str,
    subject_id: int,
    status: str,
    reviewed_by: int,
) -> None:
    if subject_type not in {"user", "chat"}:
        raise ValueError("Access subject type must be user or chat")
    if status not in {"approved", "rejected", "revoked"}:
        raise ValueError("Access request status must be approved, rejected, or revoked")

    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO access_requests (
                subject_type,
                subject_id,
                status,
                requested_at,
                reviewed_at,
                reviewed_by
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_type, subject_id) DO UPDATE SET
                status = excluded.status,
                reviewed_at = excluded.reviewed_at,
                reviewed_by = excluded.reviewed_by
            """,
            (subject_type, subject_id, status, now, now, reviewed_by),
        )


def list_approved_access() -> list[dict[str, object]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                subject_type,
                subject_id,
                username,
                first_name,
                last_name,
                chat_title,
                requested_by
            FROM access_requests
            WHERE status = 'approved'
              AND subject_type = 'user'
            ORDER BY subject_type, subject_id
            """
        ).fetchall()

    return [
        {
            "subject_type": subject_type,
            "subject_id": subject_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "chat_title": chat_title,
            "requested_by": requested_by,
        }
        for (
            subject_type,
            subject_id,
            username,
            first_name,
            last_name,
            chat_title,
            requested_by,
        ) in rows
    ]
