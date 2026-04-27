from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from googledriverag.config import AppConfig

security = HTTPBasic(auto_error=False)


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_namespace_manager(request: Request):
    return request.app.state.namespace_manager


def get_llm_client(request: Request):
    return request.app.state.llm_client


def get_embedding_client(request: Request):
    return request.app.state.embedding_client


async def verify_auth(
    credentials: HTTPBasicCredentials | None = Depends(security),
    config: AppConfig = Depends(get_config),
):
    if not config.auth.username and not config.auth.password:
        return

    if credentials is None or not (
        secrets.compare_digest(credentials.username, config.auth.username)
        and secrets.compare_digest(credentials.password, config.auth.password)
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
