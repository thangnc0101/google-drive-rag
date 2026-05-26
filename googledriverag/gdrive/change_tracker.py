from __future__ import annotations

import logging
from dataclasses import dataclass

from googledriverag.gdrive.client import DriveFile
from googledriverag.storage.namespace_storage import NamespaceStorage
from googledriverag.storage.sqlite_store import DocumentRecord

logger = logging.getLogger(__name__)


@dataclass
class SyncDelta:
    added: list[DriveFile]
    modified: list[DriveFile]
    deleted: list[DocumentRecord]


class ChangeTracker:
    def __init__(self, storage: NamespaceStorage):
        self.storage = storage

    def detect_changes(self, current_files: list[DriveFile]) -> SyncDelta:
        indexed_docs = {
            d.drive_file_id: d for d in self.storage.sqlite.list_documents()
        }
        current_map = {f.id: f for f in current_files}

        added = []
        modified = []
        deleted = []
        skipped_unchanged = 0

        for fid, file in current_map.items():
            if fid not in indexed_docs:
                added.append(file)
                logger.debug("New file detected: %s (id=%s)", file.name, fid)
            else:
                doc = indexed_docs[fid]
                needs_migration = not doc.doc_id.startswith("gdrive-")
                if needs_migration:
                    modified.append(file)
                    logger.info("Legacy doc_id detected for %s (doc_id=%s), scheduling migration", file.name, doc.doc_id)
                elif file.modifiedTime != doc.drive_modified_time:
                    modified.append(file)
                    logger.info("Modified file detected: %s (drive_time=%r, indexed_time=%r, status=%s)",
                                file.name, file.modifiedTime, doc.drive_modified_time, doc.status)
                else:
                    skipped_unchanged += 1

        for fid, doc in indexed_docs.items():
            if fid not in current_map:
                deleted.append(doc)

        logger.info("Change detection complete: %d added, %d modified, %d deleted, %d unchanged",
                     len(added), len(modified), len(deleted), skipped_unchanged)

        return SyncDelta(added=added, modified=modified, deleted=deleted)
