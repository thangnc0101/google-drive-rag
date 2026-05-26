from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from googledriverag.config import AppConfig
from googledriverag.core.chunker import Chunker
from googledriverag.core.document_parser import DocumentParser, UnsupportedFileType
from googledriverag.core.embedding_client import EmbeddingClient
from googledriverag.core.llm_client import LLMClient
from googledriverag.core.llm_enrichment import LLMEnrichment
from googledriverag.routers import chunks, documents, graph, namespaces, query, system, ui, api_calls
from googledriverag.services.ingestion_service import IngestionService
from googledriverag.services.namespace_manager import NamespaceManager, NamespaceNotFoundError
from googledriverag.services.progress_store import ProgressStore
from googledriverag.services.query_service import QueryService
from googledriverag.services.retrieval_service import RetrievalService
from googledriverag.services.sync_service import SyncService
from googledriverag.storage.api_call_store import APICallStore

logger = logging.getLogger(__name__)


async def _startup_sync(sync_service: SyncService):
    try:
        logger.info("Running startup Drive sync...")
        results = await sync_service.sync_all()
        for r in results:
            logger.info("Startup sync [%s]: added=%d updated=%d deleted=%d",
                        r.namespace, r.added, r.updated, r.deleted)
    except asyncio.CancelledError:
        logger.info("Startup sync cancelled")
    except Exception as e:
        logger.error("Startup sync failed: %s", e)


def create_app(config: AppConfig) -> FastAPI:
    llm_client = LLMClient(config.llm)
    embedding_client = EmbeddingClient(config.embedding)

    api_call_store = APICallStore(Path(config.storage.data_dir) / "api_calls.db")
    api_call_store.connect()
    llm_client.set_api_call_store(api_call_store)
    embedding_client.set_api_call_store(api_call_store)

    ns_manager = NamespaceManager(config)
    ns_manager.init_from_config()

    parser = DocumentParser()
    chunker = Chunker(config.chunking.max_chunk_tokens, config.chunking.overlap_tokens)
    enrichment = LLMEnrichment(llm_client, config.llm.max_concurrent_requests,
                               enable_gleaning=config.retrieval.enable_gleaning,
                               enable_batch_contextual=config.retrieval.enable_batch_contextual_enrichment,
                               batch_contextual_size=config.retrieval.batch_contextual_enrichment_size)
    progress_store = ProgressStore()
    ingestion = IngestionService(parser, chunker, enrichment, embedding_client,
                                 llm_client=llm_client, retrieval_config=config.retrieval,
                                 progress_store=progress_store)

    retrieval = RetrievalService(ns_manager, config.retrieval)
    query_service = QueryService(llm_client, embedding_client, retrieval, ns_manager, config.retrieval)

    sync_service = None
    if config.google_drive.credentials_file:
        from googledriverag.gdrive.client import DriveClient
        drive_client = DriveClient(config.google_drive)
        sync_service = SyncService(drive_client, ingestion, ns_manager, progress_store=progress_store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("GoogleDriveRAG starting up")
        for ns_name in ns_manager.list_all_names():
            try:
                storage = ns_manager.get_storage(ns_name)
                reset_count = storage.sqlite.reset_stuck_processing()
                if reset_count > 0:
                    logger.warning("Reset %d stuck 'processing' documents in namespace '%s'", reset_count, ns_name)
            except Exception as e:
                logger.error("Failed to reset stuck documents in %s: %s", ns_name, e)
        startup_sync_task = None
        scheduler = None
        if sync_service:
            if config.google_drive.sync_on_startup:
                startup_sync_task = asyncio.create_task(_startup_sync(sync_service))
            if config.google_drive.poll_interval_seconds > 0:
                try:
                    from apscheduler.schedulers.asyncio import AsyncIOScheduler
                    scheduler = AsyncIOScheduler()
                    scheduler.add_job(
                        sync_service.sync_all,
                        trigger="interval",
                        seconds=config.google_drive.poll_interval_seconds,
                        id="drive_sync",
                        max_instances=1,
                    )
                    scheduler.start()
                    app.state.scheduler = scheduler
                    logger.info("Drive sync scheduler started (interval=%ds)", config.google_drive.poll_interval_seconds)
                except ImportError:
                    logger.warning("apscheduler not installed, Drive sync scheduler disabled")
        yield
        logger.info("GoogleDriveRAG shutting down")
        if startup_sync_task and not startup_sync_task.done():
            startup_sync_task.cancel()
        if scheduler:
            scheduler.shutdown(wait=False)
        ns_manager.close_all()
        await llm_client.close()
        await embedding_client.close()
        api_call_store.close()

    app = FastAPI(title="GoogleDriveRAG", lifespan=lifespan)

    app.state.config = config
    app.state.llm_client = llm_client
    app.state.embedding_client = embedding_client
    app.state.namespace_manager = ns_manager
    app.state.ingestion_service = ingestion
    app.state.query_service = query_service
    app.state.retrieval_service = retrieval
    app.state.api_call_store = api_call_store
    app.state.progress_store = progress_store
    if sync_service:
        app.state.sync_service = sync_service

    app.include_router(system.router)
    app.include_router(namespaces.router, prefix="/namespaces")
    app.include_router(query.router)
    app.include_router(chunks.router)
    app.include_router(documents.router)
    app.include_router(graph.router)
    app.include_router(ui.router)
    app.include_router(api_calls.router)

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.exception_handler(NamespaceNotFoundError)
    async def ns_not_found_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(UnsupportedFileType)
    async def unsupported_file_handler(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def value_error_handler(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def generic_error_handler(request, exc):
        logger.error("Unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app
