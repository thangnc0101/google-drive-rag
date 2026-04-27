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

        for fid, file in current_map.items():
            if fid not in indexed_docs:
                added.append(file)
                logger.debug("New file detected: %s (id=%s)", file.name, fid)
            elif file.modifiedTime != indexed_docs[fid].drive_modified_time:
                modified.append(file)
                logger.debug("Modified file detected: %s (drive_time=%s, indexed_time=%s)",
                             file.name, file.modifiedTime, indexed_docs[fid].drive_modified_time)

        for fid, doc in indexed_docs.items():
            if fid not in current_map:
                deleted.append(doc)

        return SyncDelta(added=added, modified=modified, deleted=deleted)
