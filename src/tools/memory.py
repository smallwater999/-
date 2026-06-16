"""记忆库工具 - 基于 SQLite (WAL 模式 + 连接池)"""

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any
from threading import Lock

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "memory.db")
_lock = Lock()

# Module-level singleton connection pool
_conn: sqlite3.Connection | None = None
_conn_lock = Lock()


def _get_conn() -> sqlite3.Connection:
    """获取模块级单例连接 (WAL 模式, 带 busy_timeout)"""
    global _conn
    with _conn_lock:
        if _conn is None:
            os.makedirs(DB_DIR, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            # Enable WAL mode for concurrent reads + writes
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.execute("""
                CREATE TABLE IF NOT EXISTS user_memory (
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, session_id)
                )
            """)
            _conn.commit()
            logger.info(f"SQLite 记忆库已连接: {DB_PATH} (WAL 模式)")
    return _conn


def memory_save_impl(user_id: str, session_id: str, data: dict[str, Any]) -> str:
    """保存用户记忆"""
    try:
        # Validate inputs
        user_id = str(user_id)[:100]
        session_id = str(session_id)[:100]

        conn = _get_conn()
        now = datetime.now().isoformat()
        data_json = json.dumps(data, ensure_ascii=False)

        with _lock:
            conn.execute(
                "INSERT OR REPLACE INTO user_memory (user_id, session_id, data, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, session_id, data_json, now)
            )
            conn.commit()

        return json.dumps({
            "status": "success",
            "message": f"记忆已保存：用户{user_id}，阶段{data.get('stage', 1)}"
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"记忆保存失败: {e}")
        return json.dumps({
            "status": "error",
            "message": f"记忆保存失败: {str(e)[:100]}"
        }, ensure_ascii=False)


def memory_load_impl(user_id: str) -> str:
    """读取用户记忆"""
    try:
        # Validate input
        user_id = str(user_id)[:100]

        conn = _get_conn()
        rows = conn.execute(
            "SELECT session_id, data, updated_at FROM user_memory WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()

        if not rows:
            return json.dumps({
                "status": "empty",
                "message": f"用户{user_id}暂无历史记录",
                "data": None
            }, ensure_ascii=False)

        memories = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except Exception:
                data = row["data"]
            memories.append({
                "session_id": row["session_id"],
                "data": data,
                "updated_at": row["updated_at"],
            })

        return json.dumps({
            "status": "success",
            "message": f"已读取用户{user_id}的{len(memories)}条历史记录",
            "data": memories,
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"记忆读取失败: {e}")
        return json.dumps({
            "status": "error",
            "message": f"记忆读取失败: {str(e)[:100]}",
            "data": None
        }, ensure_ascii=False)
