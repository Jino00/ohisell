# config.py — 채널별 API 설정 중앙 관리
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CoupangAccountConfig:
    """쿠팡 계정 1개의 API 인증 정보"""
    vendor_id: str
    access_key: str
    secret_key: str


@dataclass(frozen=True)
class NaverAccountConfig:
    """네이버 커머스 API 인증 정보"""
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class Cafe24AccountConfig:
    """cafe24 API 인증 정보"""
    mall_id: str
    client_id: str
    client_secret: str


def get_coupang_config(api_config_key: str) -> CoupangAccountConfig | None:
    """채널의 api_config_key로 쿠팡 인증 정보 조회 (예: COUPANG_WING1)"""
    vendor_id = os.getenv(f"{api_config_key}_VENDOR_ID", "")
    access_key = os.getenv(f"{api_config_key}_ACCESS_KEY", "")
    secret_key = os.getenv(f"{api_config_key}_SECRET_KEY", "")
    if not all([vendor_id, access_key, secret_key]):
        return None
    return CoupangAccountConfig(vendor_id=vendor_id, access_key=access_key, secret_key=secret_key)


def get_naver_config(api_config_key: str) -> NaverAccountConfig | None:
    """채널의 api_config_key로 네이버 인증 정보 조회 (예: NAVER)"""
    client_id = os.getenv(f"{api_config_key}_CLIENT_ID", "")
    client_secret = os.getenv(f"{api_config_key}_CLIENT_SECRET", "")
    if not all([client_id, client_secret]):
        return None
    return NaverAccountConfig(client_id=client_id, client_secret=client_secret)


def get_cafe24_config(api_config_key: str) -> Cafe24AccountConfig | None:
    """채널의 api_config_key로 cafe24 인증 정보 조회 (예: CAFE24)"""
    mall_id = os.getenv(f"{api_config_key}_MALL_ID", "")
    client_id = os.getenv(f"{api_config_key}_CLIENT_ID", "")
    client_secret = os.getenv(f"{api_config_key}_CLIENT_SECRET", "")
    if not all([mall_id, client_id, client_secret]):
        return None
    return Cafe24AccountConfig(mall_id=mall_id, client_id=client_id, client_secret=client_secret)


def get_ad_data_db_path() -> str | None:
    """ohi-ad-intelligence ad_data.db 경로"""
    path = os.getenv("AD_DATA_DB_PATH", "")
    if not path or not os.path.isfile(path):
        return None
    return path
