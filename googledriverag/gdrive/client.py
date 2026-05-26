from __future__ import annotations

import logging
from dataclasses import dataclass

from googledriverag.config import GoogleDriveConfig

logger = logging.getLogger(__name__)


@dataclass
class DriveFile:
    id: str
    name: str
    mimeType: str
    modifiedTime: str
    size: int | None = None
    webViewLink: str = ""


class DriveClient:
    def __init__(self, config: GoogleDriveConfig):
        self.config = config
        self.service = None

    def _ensure_service(self):
        if self.service is not None:
            return
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            self.config.credentials_file,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        self.service = build("drive", "v3", credentials=creds)

    def _parse_size(self, raw) -> int | None:
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def list_files(self, folder_id: str) -> list[DriveFile]:
        self._ensure_service()
        query = f"'{folder_id}' in parents and trashed = false"
        results = []
        page_token = None
        max_size_bytes = int(self.config.max_file_size_mb * 1024 * 1024)
        while True:
            resp = self.service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, webViewLink)",
                pageToken=page_token,
            ).execute()
            for f in resp.get("files", []):
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    results.extend(self.list_files(f["id"]))
                elif f["mimeType"] in self.config.file_types:
                    size = self._parse_size(f.get("size"))
                    if size is not None and size > max_size_bytes:
                        logger.warning(
                            "Skipping file %s (id=%s): size %.2f MB exceeds max_file_size_mb=%.2f",
                            f["name"], f["id"], size / (1024 * 1024),
                            self.config.max_file_size_mb,
                        )
                        continue
                    results.append(DriveFile(
                        id=f["id"], name=f["name"], mimeType=f["mimeType"],
                        modifiedTime=f["modifiedTime"], size=size,
                        webViewLink=f.get("webViewLink", ""),
                    ))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def list_oversized_files(self, folder_id: str) -> list[DriveFile]:
        self._ensure_service()
        query = f"'{folder_id}' in parents and trashed = false"
        results: list[DriveFile] = []
        page_token = None
        max_size_bytes = int(self.config.max_file_size_mb * 1024 * 1024)
        while True:
            resp = self.service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, webViewLink)",
                pageToken=page_token,
            ).execute()
            for f in resp.get("files", []):
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    results.extend(self.list_oversized_files(f["id"]))
                elif f["mimeType"] in self.config.file_types:
                    size = self._parse_size(f.get("size"))
                    if size is not None and size > max_size_bytes:
                        results.append(DriveFile(
                            id=f["id"], name=f["name"], mimeType=f["mimeType"],
                            modifiedTime=f["modifiedTime"], size=size,
                            webViewLink=f.get("webViewLink", ""),
                        ))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def get_file_metadata(self, file_id: str) -> DriveFile | None:
        self._ensure_service()
        try:
            f = self.service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, modifiedTime, size, webViewLink",
            ).execute()
            return DriveFile(
                id=f["id"], name=f["name"], mimeType=f["mimeType"],
                modifiedTime=f["modifiedTime"], size=self._parse_size(f.get("size")),
                webViewLink=f.get("webViewLink", ""),
            )
        except Exception:
            logger.error("Failed to get metadata for file %s", file_id)
            return None

    def download_file(self, file_id: str, mime_type: str) -> bytes:
        self._ensure_service()
        if mime_type.startswith("application/vnd.google-apps."):
            return self.service.files().export(
                fileId=file_id, mimeType="text/plain"
            ).execute()
        else:
            import io
            from googleapiclient.http import MediaIoBaseDownload

            request = self.service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buffer.getvalue()
