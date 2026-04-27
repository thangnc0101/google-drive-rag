from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from googledriverag.gdrive.change_tracker import ChangeTracker
from googledriverag.gdrive.client import DriveClient, DriveFile
from googledriverag.services.ingestion_service import IngestionService
from googledriverag.services.namespace_manager import NamespaceManager
from googledriverag.services.progress_store import ProgressStore
from googledriverag.storage.namespace_storage import NamespaceStorage

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    namespace: str
    added: int
    updated: int
    deleted: int


class SyncService:
    def __init__(
        self,
        drive_client: DriveClient,
        ingestion: IngestionService,
        ns_manager: NamespaceManager,
        progress_store: ProgressStore | None = None,
    ):
        self.drive = drive_client
        self.ingestion = ingestion
        self.ns_manager = ns_manager
        self.progress = progress_store
        self._locks: dict[str, asyncio.Lock] = {}

    async def sync_namespace(self, ns_name: str) -> SyncResult:
        lock = self._locks.setdefault(ns_name, asyncio.Lock())
        async with lock:
            ns_config = self.ns_manager.get_config(ns_name)
            storage = self.ns_manager.get_storage(ns_name)

            all_files: list[DriveFile] = []
            for folder_id in ns_config.folder_ids:
                try:
                    all_files.extend(self.drive.list_files(folder_id))
                except Exception as e:
                    logger.error("Failed to list folder %s: %s", folder_id, e)

            tracker = ChangeTracker(storage)
            delta = tracker.detect_changes(all_files)
            logger.debug("Sync %s: %d files from Drive, delta: +%d modified=%d -%d",
                         ns_name, len(all_files), len(delta.added), len(delta.modified), len(delta.deleted))

            for doc in delta.deleted:
                self._delete_document(storage, doc.doc_id)
                logger.info("Deleted document %s from %s", doc.name, ns_name)

            ingest_files = list(delta.modified) + list(delta.added)
            total_ingest = len(ingest_files)
            if self.progress and total_ingest > 0:
                self.progress.start_sync(ns_name, total_ingest)

            try:
                file_idx = 0
                for file in delta.modified:
                    doc = storage.sqlite.get_document_by_drive_id(file.id)
                    if doc:
                        self._delete_document(storage, doc.doc_id)
                    await self._ingest_file(storage, file)
                    logger.info("Updated document %s in %s", file.name, ns_name)
                    file_idx += 1
                    if self.progress:
                        self.progress.update_sync_file(ns_name, file_idx)

                for file in delta.added:
                    await self._ingest_file(storage, file)
                    logger.info("Added document %s to %s", file.name, ns_name)
                    file_idx += 1
                    if self.progress:
                        self.progress.update_sync_file(ns_name, file_idx)
            finally:
                if self.progress:
                    self.progress.finish_sync(ns_name)

            if delta.deleted or delta.modified or delta.added:
                storage.graph.detect_communities()
                storage.graph.save()

            return SyncResult(
                namespace=ns_name,
                added=len(delta.added),
                updated=len(delta.modified),
                deleted=len(delta.deleted),
            )

    async def sync_all(self) -> list[SyncResult]:
        tasks = [self.sync_namespace(ns) for ns in self.ns_manager.list_all_names()]
        return await asyncio.gather(*tasks)

    async def _ingest_file(self, storage: NamespaceStorage, file: DriveFile):
        try:
            file_bytes = self.drive.download_file(file.id, file.mimeType)
            await self.ingestion.ingest_document(
                file_bytes=file_bytes,
                filename=file.name,
                mime_type=file.mimeType,
                drive_file_id=file.id,
                storage=storage,
                drive_modified_time=file.modifiedTime,
                url=file.webViewLink,
            )
        except Exception as e:
            logger.error("Failed to ingest %s: %s", file.name, e)

    def _delete_document(self, storage: NamespaceStorage, doc_id: str):
        chunk_ids = [
            r.chunk_id for r in storage.sqlite.get_chunks_by_doc(doc_id)
        ]

        orphaned_entities, orphaned_relations = (
            storage.sqlite.find_orphaned_after_chunk_delete(chunk_ids)
        )

        storage.sqlite.delete_chunks_by_doc(doc_id)
        for cid in chunk_ids:
            storage.vectors.chunks.delete(cid)

        for entity_name in orphaned_entities:
            storage.graph.remove_node(entity_name)
            storage.vectors.entities.delete(entity_name)

        for src, tgt in orphaned_relations:
            storage.graph.remove_edge(src, tgt)
            storage.vectors.relationships.delete(f"{src}||{tgt}")

        storage.sqlite.delete_document_row(doc_id)
        storage.vectors.save()
