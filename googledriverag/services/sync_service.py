from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from googledriverag.gdrive.change_tracker import ChangeTracker
from googledriverag.gdrive.client import DriveClient, DriveFile
from googledriverag.core.errors import ExternalAPIError
from googledriverag.services.ingestion_service import IngestResult, IngestionService
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
            failed_folders: list[str] = []
            for folder_id in ns_config.folder_ids:
                try:
                    all_files.extend(self.drive.list_files(folder_id))
                except Exception as e:
                    failed_folders.append(folder_id)
                    logger.error("Failed to list folder %s: %s", folder_id, e)

            if failed_folders:
                logger.warning(
                    "Skipping sync for namespace %s: %d/%d folder listings failed (%s); "
                    "aborting to prevent accidental cascade deletion",
                    ns_name, len(failed_folders), len(ns_config.folder_ids), failed_folders,
                )
                return SyncResult(namespace=ns_name, added=0, updated=0, deleted=0)

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

            has_content_changes = len(delta.deleted) > 0
            api_aborted = False
            try:
                file_idx = 0
                for file in delta.modified:
                    try:
                        result = await self._ingest_file(storage, file)
                    except ExternalAPIError as e:
                        logger.warning(
                            "Aborting sync for namespace %s due to external API error: %s. "
                            "Remaining %d files will be skipped.",
                            ns_name, e, len(delta.modified) + len(delta.added) - file_idx,
                        )
                        api_aborted = True
                        break
                    if result is None:
                        logger.warning("Failed to process %s in %s", file.name, ns_name)
                    elif result.chunks_count > 0:
                        has_content_changes = True
                        logger.info("Updated document %s in %s (content changed)", file.name, ns_name)
                    else:
                        logger.info("Updated metadata for %s in %s (content unchanged)", file.name, ns_name)
                    file_idx += 1
                    if self.progress:
                        self.progress.update_sync_file(ns_name, file_idx)

                if not api_aborted:
                    for file in delta.added:
                        try:
                            result = await self._ingest_file(storage, file)
                        except ExternalAPIError as e:
                            logger.warning(
                                "Aborting sync for namespace %s due to external API error: %s. "
                                "Remaining %d files will be skipped.",
                                ns_name, e, len(delta.added) - (file_idx - len(delta.modified)),
                            )
                            api_aborted = True
                            break
                        if result and result.chunks_count > 0:
                            has_content_changes = True
                        if result:
                            logger.info("Added document %s to %s", file.name, ns_name)
                        file_idx += 1
                        if self.progress:
                            self.progress.update_sync_file(ns_name, file_idx)
            finally:
                if self.progress:
                    self.progress.finish_sync(ns_name)

            if has_content_changes:
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

    async def _ingest_file(self, storage: NamespaceStorage, file: DriveFile, force_reindex: bool = False) -> IngestResult | None:
        try:
            file_bytes = self.drive.download_file(file.id, file.mimeType)
            return await self.ingestion.ingest_document(
                file_bytes=file_bytes,
                filename=file.name,
                mime_type=file.mimeType,
                drive_file_id=file.id,
                storage=storage,
                drive_modified_time=file.modifiedTime,
                url=file.webViewLink,
                force_reindex=force_reindex,
            )
        except ExternalAPIError:
            raise
        except Exception as e:
            logger.error("Failed to ingest %s (drive_id=%s, force=%s): %s", file.name, file.id, force_reindex, e)
            return None

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
