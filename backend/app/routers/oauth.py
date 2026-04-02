# oauth.py — cafe24 OAuth2 인증 플로우 라우터
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.clients.cafe24 import build_cafe24_oauth_url, exchange_authorization_code
from app.config import get_cafe24_config
from app.database import get_db
from app.models import Channel, OAuthToken
from app.schemas import OAuthAuthUrl, OAuthStatus

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

# cafe24 OAuth callback 후 프론트엔드로 리다이렉트할 URL
FRONTEND_BASE = os.getenv("FRONTEND_URL", "http://localhost:5173")


def _get_cafe24_channel(db: Session) -> Channel | None:
    return db.query(Channel).filter(Channel.code == "CAFE24").first()


def _get_redirect_uri(request: Request) -> str:
    """요청 기반으로 callback URL 동적 생성"""
    return str(request.base_url).rstrip("/") + "/api/oauth/cafe24/callback"


@router.get("/cafe24/auth-url", response_model=OAuthAuthUrl)
def get_cafe24_auth_url(request: Request):
    """cafe24 OAuth 인증 URL 생성"""
    config = get_cafe24_config("CAFE24")
    if not config:
        return OAuthAuthUrl(auth_url="", mall_id="")

    redirect_uri = _get_redirect_uri(request)
    auth_url = build_cafe24_oauth_url(config.mall_id, config.client_id, redirect_uri)
    return OAuthAuthUrl(auth_url=auth_url, mall_id=config.mall_id)


@router.get("/cafe24/callback")
def cafe24_oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """cafe24 OAuth callback — authorization code를 토큰으로 교환 후 DB 저장"""
    config = get_cafe24_config("CAFE24")
    if not config:
        return RedirectResponse(f"{FRONTEND_BASE}/?oauth=error&message=config_missing")

    channel = _get_cafe24_channel(db)
    if not channel:
        return RedirectResponse(f"{FRONTEND_BASE}/?oauth=error&message=channel_missing")

    redirect_uri = _get_redirect_uri(request)
    token_data = exchange_authorization_code(
        mall_id=config.mall_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
        code=code,
        redirect_uri=redirect_uri,
    )

    if not token_data or not token_data.get("access_token"):
        log.error("cafe24 토큰 교환 실패")
        return RedirectResponse(f"{FRONTEND_BASE}/?oauth=error&message=token_exchange_failed")

    # OAuthToken upsert
    existing = db.query(OAuthToken).filter(OAuthToken.channel_id == channel.id).first()
    if existing:
        existing.access_token = token_data["access_token"]
        existing.refresh_token = token_data.get("refresh_token")
        existing.expires_at = token_data.get("expires_at")
        existing.refresh_token_expires_at = token_data.get("refresh_token_expires_at")
    else:
        new_token = OAuthToken(
            channel_id=channel.id,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            token_type="Bearer",
            expires_at=token_data.get("expires_at"),
            refresh_token_expires_at=token_data.get("refresh_token_expires_at"),
        )
        db.add(new_token)

    db.commit()
    log.info("cafe24 OAuth 토큰 저장 완료 (channel_id=%d)", channel.id)
    return RedirectResponse(f"{FRONTEND_BASE}/?oauth=success")


@router.get("/cafe24/status", response_model=OAuthStatus)
def get_cafe24_status(db: Session = Depends(get_db)):
    """cafe24 OAuth 연동 상태 확인"""
    config = get_cafe24_config("CAFE24")
    if not config:
        return OAuthStatus(status="not_connected", message="cafe24 설정이 없습니다")

    channel = _get_cafe24_channel(db)
    if not channel:
        return OAuthStatus(status="not_connected", message="cafe24 채널이 없습니다")

    token_row = db.query(OAuthToken).filter(OAuthToken.channel_id == channel.id).first()
    if not token_row:
        return OAuthStatus(
            status="not_connected",
            mall_id=config.mall_id,
            message="OAuth 인증이 필요합니다",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # refresh token 만료 확인
    if token_row.refresh_token_expires_at and token_row.refresh_token_expires_at <= now:
        return OAuthStatus(
            status="expired",
            mall_id=config.mall_id,
            expires_at=_fmt(token_row.expires_at),
            refresh_token_expires_at=_fmt(token_row.refresh_token_expires_at),
            message="Refresh Token이 만료되었습니다. 재인증이 필요합니다.",
        )

    # access token 만료 확인 (refresh로 갱신 가능하므로 connected)
    status = "connected"
    message = None
    if token_row.expires_at and token_row.expires_at <= now:
        message = "Access Token 만료됨 — 다음 API 호출 시 자동 갱신됩니다"

    return OAuthStatus(
        status=status,
        mall_id=config.mall_id,
        expires_at=_fmt(token_row.expires_at),
        refresh_token_expires_at=_fmt(token_row.refresh_token_expires_at),
        message=message,
    )


@router.post("/cafe24/disconnect")
def disconnect_cafe24(db: Session = Depends(get_db)):
    """cafe24 OAuth 연동 해제 (토큰 삭제)"""
    channel = _get_cafe24_channel(db)
    if not channel:
        return {"status": "ok", "message": "cafe24 채널이 없습니다"}

    deleted = db.query(OAuthToken).filter(OAuthToken.channel_id == channel.id).delete()
    db.commit()

    if deleted:
        log.info("cafe24 OAuth 토큰 삭제 완료")
        return {"status": "ok", "message": "cafe24 연동이 해제되었습니다"}
    return {"status": "ok", "message": "저장된 토큰이 없습니다"}


def _fmt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()
