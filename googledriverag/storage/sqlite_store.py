from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class DocumentRecord:
    doc_id: str
    name: str
    drive_file_id: str = ""
    drive_modified_time: str = ""
    content_hash: str = ""
    status: str = "indexed"
    synced_at: str = ""
    url: str = ""
    file_size: int = 0


@dataclass
class ChunkRecord:
    chunk_id: str
    doc_id: str
    sequence_index: int
    text: str
    page: int | None = None
    section: str | None = None
    token_count: int = 0
    keywords: str = "[]"
    expanded_keywords: str = "[]"
    summary: str = ""
    category: str = ""


_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    name TEXT,
    drive_file_id TEXT,
    drive_modified_time TEXT,
    content_hash TEXT,
    status TEXT,
    synced_at TEXT,
    url TEXT DEFAULT '',
    file_size INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT REFERENCES documents(doc_id),
    sequence_index INTEGER,
    text TEXT,
    page INTEGER,
    section TEXT,
    token_count INTEGER,
    keywords TEXT,
    expanded_keywords TEXT,
    summary TEXT,
    category TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, keywords, expanded_keywords, summary,
    content=chunks, content_rowid=rowid
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, keywords, expanded_keywords, summary)
    VALUES (new.rowid, new.text, new.keywords, new.expanded_keywords, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, keywords, expanded_keywords, summary)
    VALUES ('delete', old.rowid, old.text, old.keywords, old.expanded_keywords, old.summary);
END;
"""

_CREATE_TABLES_SQL_2 = """
CREATE TABLE IF NOT EXISTS entity_chunks (
    entity_name TEXT,
    chunk_id TEXT,
    PRIMARY KEY (entity_name, chunk_id)
);

CREATE TABLE IF NOT EXISTS relation_chunks (
    src_entity TEXT,
    tgt_entity TEXT,
    chunk_id TEXT,
    PRIMARY KEY (src_entity, tgt_entity, chunk_id)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY,
    response TEXT,
    created_at TEXT
);
"""


class SQLiteStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_CREATE_TABLES_SQL)
        self.conn.executescript(_CREATE_TABLES_SQL_2)
        self._migrate_add_url_column()
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _migrate_add_url_column(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(documents)").fetchall()]
        if "url" not in cols:
            self.conn.execute("ALTER TABLE documents ADD COLUMN url TEXT DEFAULT ''")
        if "file_size" not in cols:
            self.conn.execute("ALTER TABLE documents ADD COLUMN file_size INTEGER DEFAULT 0")

    def upsert_document(self, doc: DocumentRecord) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, name, drive_file_id, drive_modified_time, content_hash, status, synced_at, url, file_size)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc.doc_id, doc.name, doc.drive_file_id, doc.drive_modified_time,
             doc.content_hash, doc.status, doc.synced_at, doc.url, doc.file_size),
        )
        self.conn.commit()

    def update_document_status(self, doc_id: str, status: str) -> None:
        self.conn.execute("UPDATE documents SET status = ? WHERE doc_id = ?", (status, doc_id))
        self.conn.commit()

    def reset_stuck_processing(self) -> int:
        cursor = self.conn.execute(
            "UPDATE documents SET status = 'error' WHERE status = 'processing'"
        )
        self.conn.commit()
        return cursor.rowcount

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        row = self.conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is None:
            return None
        return DocumentRecord(**dict(row))

    def get_document_by_drive_id(self, drive_file_id: str) -> DocumentRecord | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE drive_file_id = ?", (drive_file_id,)
        ).fetchone()
        if row is None:
            return None
        return DocumentRecord(**dict(row))

    def list_documents(self) -> list[DocumentRecord]:
        rows = self.conn.execute("SELECT * FROM documents").fetchall()
        return [DocumentRecord(**dict(r)) for r in rows]

    def delete_document(self, doc_id: str) -> int:
        chunk_ids = self.delete_chunks_by_doc(doc_id)
        self.conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self.conn.commit()
        return len(chunk_ids)

    def delete_document_row(self, doc_id: str) -> None:
        self.conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self.conn.commit()

    def insert_chunks(self, chunks: list[ChunkRecord]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO chunks
               (chunk_id, doc_id, sequence_index, text, page, section,
                token_count, keywords, expanded_keywords, summary, category)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (c.chunk_id, c.doc_id, c.sequence_index, c.text, c.page, c.section,
                 c.token_count, c.keywords, c.expanded_keywords, c.summary, c.category)
                for c in chunks
            ],
        )
        self.conn.commit()

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        row = self.conn.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        if row is None:
            return None
        return ChunkRecord(**dict(row))

    def get_chunks_by_doc(self, doc_id: str) -> list[ChunkRecord]:
        rows = self.conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY sequence_index", (doc_id,)
        ).fetchall()
        return [ChunkRecord(**dict(r)) for r in rows]

    def get_chunk_context(self, chunk_id: str, before: int, after: int) -> list[ChunkRecord]:
        target = self.get_chunk(chunk_id)
        if target is None:
            return []
        start = max(0, target.sequence_index - before)
        end = target.sequence_index + after
        rows = self.conn.execute(
            """SELECT * FROM chunks
               WHERE doc_id = ? AND sequence_index BETWEEN ? AND ?
               ORDER BY sequence_index""",
            (target.doc_id, start, end),
        ).fetchall()
        return [ChunkRecord(**dict(r)) for r in rows]

    def get_chunk_siblings(self, chunk_id: str) -> list[ChunkRecord]:
        target = self.get_chunk(chunk_id)
        if target is None:
            return []
        return self.get_chunks_by_doc(target.doc_id)

    def delete_chunks_by_doc(self, doc_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchall()
        chunk_ids = [r["chunk_id"] for r in rows]
        if chunk_ids:
            self.delete_mappings_by_chunks(chunk_ids)
            self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            self.conn.commit()
        return chunk_ids

    def keyword_search(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        try:
            sanitized = '"' + query.replace('"', '""') + '"'
            rows = self.conn.execute(
                """SELECT c.chunk_id, f.rank
                   FROM chunks_fts f
                   JOIN chunks c ON c.rowid = f.rowid
                   WHERE chunks_fts MATCH ?
                   ORDER BY f.rank
                   LIMIT ?""",
                (sanitized, limit),
            ).fetchall()
            return [(r["chunk_id"], r["rank"]) for r in rows]
        except sqlite3.OperationalError:
            return []

    def upsert_entity_chunks(self, entity_name: str, chunk_ids: list[str]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO entity_chunks (entity_name, chunk_id) VALUES (?, ?)",
            [(entity_name, cid) for cid in chunk_ids],
        )
        self.conn.commit()

    def get_entity_chunk_ids(self, entity_name: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT chunk_id FROM entity_chunks WHERE entity_name = ?", (entity_name,)
        ).fetchall()
        return [r["chunk_id"] for r in rows]

    def upsert_relation_chunks(self, src: str, tgt: str, chunk_ids: list[str]) -> None:
        key = tuple(sorted([src, tgt]))
        self.conn.executemany(
            "INSERT OR IGNORE INTO relation_chunks (src_entity, tgt_entity, chunk_id) VALUES (?, ?, ?)",
            [(key[0], key[1], cid) for cid in chunk_ids],
        )
        self.conn.commit()

    def get_relation_chunk_ids(self, src: str, tgt: str) -> list[str]:
        key = tuple(sorted([src, tgt]))
        rows = self.conn.execute(
            "SELECT chunk_id FROM relation_chunks WHERE src_entity = ? AND tgt_entity = ?",
            (key[0], key[1]),
        ).fetchall()
        return [r["chunk_id"] for r in rows]

    def find_orphaned_after_chunk_delete(
        self, chunk_ids: list[str]
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Find entities and relations that will become orphaned after removing chunk_ids.

        Returns (orphaned_entity_names, orphaned_relation_keys) where each
        relation key is a (src, tgt) tuple (already sorted).
        """
        if not chunk_ids:
            return [], []
        placeholders = ",".join("?" for _ in chunk_ids)
        # Entities linked to these chunks
        rows = self.conn.execute(
            f"SELECT DISTINCT entity_name FROM entity_chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        candidate_entities = [r["entity_name"] for r in rows]
        # Relations linked to these chunks
        rows = self.conn.execute(
            f"SELECT DISTINCT src_entity, tgt_entity FROM relation_chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        candidate_relations = [(r["src_entity"], r["tgt_entity"]) for r in rows]

        orphaned_entities: list[str] = []
        for name in candidate_entities:
            remaining = self.conn.execute(
                f"SELECT 1 FROM entity_chunks WHERE entity_name = ? AND chunk_id NOT IN ({placeholders}) LIMIT 1",
                [name, *chunk_ids],
            ).fetchone()
            if not remaining:
                orphaned_entities.append(name)

        orphaned_relations: list[tuple[str, str]] = []
        for src, tgt in candidate_relations:
            remaining = self.conn.execute(
                f"SELECT 1 FROM relation_chunks WHERE src_entity = ? AND tgt_entity = ? AND chunk_id NOT IN ({placeholders}) LIMIT 1",
                [src, tgt, *chunk_ids],
            ).fetchone()
            if not remaining:
                orphaned_relations.append((src, tgt))

        return orphaned_entities, orphaned_relations

    def delete_mappings_by_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        placeholders = ",".join("?" for _ in chunk_ids)
        self.conn.execute(
            f"DELETE FROM entity_chunks WHERE chunk_id IN ({placeholders})", chunk_ids
        )
        self.conn.execute(
            f"DELETE FROM relation_chunks WHERE chunk_id IN ({placeholders})", chunk_ids
        )
        self.conn.commit()

    def get_cache(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT response FROM llm_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        return row["response"] if row else None

    def set_cache(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache (cache_key, response, created_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        doc_count = self.conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
        chunk_count = self.conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
        return {"document_count": doc_count, "chunk_count": chunk_count}

    def count_chunks_by_doc(self, doc_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as c FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return row["c"]
