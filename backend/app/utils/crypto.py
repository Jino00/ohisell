# crypto.py — 민감정보(Wing 세션쿠키) Fernet 대칭 암호화 유틸 (트랙 D-5, S1-b)
# 키 = .env COOKIE_ENC_KEY (Fernet.generate_key() 1회 생성 → 서버 .env에 보관, 로그 노출 금지).
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["encrypt_secret", "decrypt_secret", "CookieCryptoError"]


class CookieCryptoError(Exception):
    """쿠키 암복호화 실패 — 키 미설정(COOKIE_ENC_KEY) 또는 토큰 손상(InvalidToken).

    호출자(라우터/SA)가 이 예외를 잡아 사용자에게 '암호화 키 설정 필요/쿠키 재입력 필요'로 안내.
    """


def _fernet() -> Fernet:
    key = os.getenv("COOKIE_ENC_KEY", "").strip()
    if not key:
        raise CookieCryptoError(
            "COOKIE_ENC_KEY 미설정 — Wing 쿠키 암복호화 불가. "
            ".env에 Fernet 키 추가 필요(python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\")."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:  # 잘못된 키 형식
        raise CookieCryptoError(f"COOKIE_ENC_KEY 형식 오류(Fernet 키 아님): {e}") from e


def encrypt_secret(plain: str) -> str:
    """평문 → Fernet 암호문(문자열). 빈 문자열은 그대로 통과(저장 안 함 의미)."""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Fernet 암호문 → 평문. 빈 문자열은 그대로. 손상/키불일치는 CookieCryptoError."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise CookieCryptoError("쿠키 복호화 실패(키 불일치·토큰 손상) — 쿠키 재입력 필요") from e
