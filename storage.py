import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Any

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
        
        # 檢查並建立 access_requests
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
    return row[0] if row else None

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

# --- 核心修改：回傳原始格式以利 utils.py 處理 ---
def load_history(user_id: int, limit_turns: int | None = None) -> list[tuple[str, str]]:
    """
    修改點：回傳 (role, content) 元組列表。
    避免在 storage 層包裝 SDK 物件，交由 utils.py 統一處理。
    """
    if limit_turns is None:
        limit_turns = conf.get("max_history_turns", 10)
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
    return [(row[0], row[1]) for row in rows]

def append_turn(
    user_id: int,
    model: str,
    user_text: str,
    model_text: str,
    max_turns: int | None = None,
) -> None:
    if max_turns is None:
        max_turns = conf.get("max_history_turns", 10)
    now = _now()
    keep_count = max_turns * 2
    with _connect() as conn:
        # 更新 session
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
        # 插入對話
        conn.execute(
            "INSERT INTO chat_messages (user_id, role, content, model, created_at) VALUES (?, 'user', ?, ?, ?)",
            (user_id, user_text, model, now),
        )
        conn.execute(
            "INSERT INTO chat_messages (user_id, role, content, model, created_at) VALUES (?, 'model', ?, ?, ?)",
            (user_id, model_text, model, now),
        )
        # 清理多餘歷史
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

# --- 其他設置函數 ---
def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM bot_settings WHERE key = ?",
            (key,),
        ).fetchone()
    return row[0] if row else default

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

def get_access_status(subject_type: str, subject_id: int) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM access_requests WHERE subject_type = ? AND subject_id = ?",
            (subject_type, subject_id),
        ).fetchone()
    return row[0] if row else None
