# channels.py — 채널 목록 + 연동 상태 API
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.config import get_cafe24_config, get_coupang_config, get_naver_config
from app.database import get_db
from app.models import Channel, OAuthToken, SyncLog
from app.schemas import ChannelOut

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.get("", response_model=list[ChannelOut])
def list_channels(db: Session = Depends(get_db)):
    return db.query(Channel).order_by(Channel.id).all()


@router.get("/connection-status")
def channel_connection_status(db: Session = Depends(get_db)):
    """전체 채널의 API 연동 상태 + 마지막 동기화 정보"""
    channels = db.query(Channel).order_by(Channel.id).all()
    result = []

    for ch in channels:
        item: dict = {
            "channel_id": ch.id,
            "channel_name": ch.name,
            "code": ch.code,
            "platform": ch.platform,
            "api_type": ch.api_type,
            "api_configured": False,
            "oauth_status": None,
            "oauth_expires_at": None,
            "refresh_token_expires_at": None,
            "last_sync_at": None,
            "last_sync_status": None,
            "last_sync_records": 0,
        }

        # API 키 설정 여부
        if ch.api_config_key:
            if ch.platform == "coupang":
                item["api_configured"] = get_coupang_config(ch.api_config_key) is not None
            elif ch.platform == "naver":
                item["api_configured"] = get_naver_config(ch.api_config_key) is not None
            elif ch.platform == "cafe24":
                item["api_configured"] = get_cafe24_config(ch.api_config_key) is not None

        # cafe24 OAuth 상태
        if ch.platform == "cafe24":
            token_row = db.query(OAuthToken).filter(OAuthToken.channel_id == ch.id).first()
            if token_row:
                now = datetime.utcnow()
                if token_row.refresh_token_expires_at and token_row.refresh_token_expires_at <= now:
                    item["oauth_status"] = "expired"
                else:
                    item["oauth_status"] = "connected"
                item["oauth_expires_at"] = token_row.expires_at.isoformat() if token_row.expires_at else None
                item["refresh_token_expires_at"] = (
                    token_row.refresh_token_expires_at.isoformat()
                    if token_row.refresh_token_expires_at else None
                )
            else:
                item["oauth_status"] = "not_connected"

        # 마지막 동기화
        last_log = (
            db.query(SyncLog)
            .filter(SyncLog.channel_id == ch.id)
            .order_by(desc(SyncLog.started_at))
            .first()
        )
        if last_log:
            item["last_sync_at"] = last_log.completed_at.isoformat() if last_log.completed_at else None
            item["last_sync_status"] = last_log.status
            item["last_sync_records"] = last_log.records_synced or 0

        result.append(item)

    return result
