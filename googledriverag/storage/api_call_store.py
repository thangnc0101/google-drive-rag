from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class APICallRecord:
    id: str
    call_type: str  # "llm" or "embedding"
    model: str
    document_name: str
    chunk_id: str
    operation: str  # "enrichment", "keyword_extraction", "answer_generation", "embed_chunk", "embed_entity", etc.
    input_tokens: int
    output_tokens: int
    created_at: str
    namespace: str = ""


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS api_calls (
    id TEXT PRIMARY KEY,
    call_type TEXT NOT NULL,
    model TEXT NOT NULL,
    document_name TEXT DEFAULT '',
    chunk_id TEXT DEFAULT '',
    operation TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    namespace TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_api_calls_created_at ON api_calls(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_calls_type ON api_calls(call_type);
"""


class APICallStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        self.conn.executescript(_CREATE_TABLE_SQL)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_api_calls_namespace ON api_calls(namespace)")
        self.conn.commit()

    def _migrate(self):
        table_exists = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_calls'"
        ).fetchone()
        if not table_exists:
            return
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(api_calls)").fetchall()}
        if "namespace" not in cols:
            self.conn.execute("ALTER TABLE api_calls ADD COLUMN namespace TEXT DEFAULT ''")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def record_call(
        self,
        call_type: str,
        model: str,
        operation: str = "",
        document_name: str = "",
        chunk_id: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        namespace: str = "",
    ) -> str:
        call_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO api_calls
               (id, call_type, model, document_name, chunk_id, operation,
                input_tokens, output_tokens, created_at, namespace)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (call_id, call_type, model, document_name, chunk_id, operation,
             input_tokens, output_tokens, now, namespace),
        )
        self.conn.commit()
        return call_id

    def list_calls(self, limit: int = 100, offset: int = 0,
                   call_type: str | None = None,
                   namespace: str | None = None) -> list[APICallRecord]:
        conditions = []
        params: list = []
        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)
        if namespace:
            conditions.append("namespace = ?")
            params.append(namespace)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        rows = self.conn.execute(
            f"SELECT * FROM api_calls{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [APICallRecord(**dict(r)) for r in rows]

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) as c FROM api_calls").fetchone()["c"]
        llm_count = self.conn.execute(
            "SELECT COUNT(*) as c FROM api_calls WHERE call_type = 'llm'"
        ).fetchone()["c"]
        embedding_count = self.conn.execute(
            "SELECT COUNT(*) as c FROM api_calls WHERE call_type = 'embedding'"
        ).fetchone()["c"]
        total_input = self.conn.execute(
            "SELECT COALESCE(SUM(input_tokens), 0) as s FROM api_calls"
        ).fetchone()["s"]
        total_output = self.conn.execute(
            "SELECT COALESCE(SUM(output_tokens), 0) as s FROM api_calls"
        ).fetchone()["s"]
        return {
            "total_calls": total,
            "llm_calls": llm_count,
            "embedding_calls": embedding_count,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
        }

    def clear(self) -> int:
        count = self.conn.execute("SELECT COUNT(*) as c FROM api_calls").fetchone()["c"]
        self.conn.execute("DELETE FROM api_calls")
        self.conn.commit()
        return count
