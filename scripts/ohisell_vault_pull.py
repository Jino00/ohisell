#!/usr/bin/env python3
# 이 파일은 ohisell 운영 일기·지혜 볼트(VM 정본)를 Mac iCloud Obsidian으로 순방향 미러하는
# pull 스크립트입니다 (D-NAO-54 P5 열람층, docs/PLAN_naver-ad-diary-wisdom.md §P5).
#
# AI_office scripts/obsidian_mac_bridge.py의 "순방향 전용 축소판":
#   - 이식: rsync pull + iCloud dataless(EDEADLK) 자가치유(brctl download 후 재시도) + 단일
#     실행 종료(launchd StartInterval) + lockfile 미사용은 rsync 멱등성으로 대체.
#   - 생략: 3-way merge / baseline / 역방향 push / zone marker / OBSIDIAN_SYNC_KEY — VM이 항상
#     정본이고 Jino는 이 볼트를 편집하지 않는(열람 전용) 단방향이라 병합·역방향이 통째로 불필요.
#
# 동작: VM sellc.ohitech.co.kr:/home/ubuntu/ohisell/backend/data/vault/Ohisell/ → Mac Vault/Ohisell/
#   를 rsync -az(--delete 없이)로 pull. rsync가 실패하면 로컬 볼트를 brctl download로 실체화한 뒤
#   1회 재시도(evict된 dataless 파일이 rsync의 로컬 비교를 EDEADLK로 막는 경우 대비, failures.jsonl
#   2026-07-17 참조). 로그는 ~/Library/Logs/ohisell-vault-pull.log에 append(1MB 롤오버).
#
# 설치(오케스트레이터가 수행): scripts/com.ohisell.vaultpull.plist 상단 주석 참조.
from __future__ import annotations

import logging
import logging.handlers
import subprocess
import sys
from pathlib import Path

# ─── 경로 설정 ──────────────────────────────────────────────────────────────
VM_HOST = "sellc.ohitech.co.kr"  # ssh config가 user 매핑(safe_deploy.sh와 동일 관례)
VM_VAULT_PATH = "/home/ubuntu/ohisell/backend/data/vault/Ohisell"

LOCAL_VAULT_ROOT = (
    Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Vault/Ohisell"
)

LOG_FILE = Path.home() / "Library/Logs/ohisell-vault-pull.log"
_LOG_MAX_BYTES = 1_000_000  # 1MB 롤오버
_LOG_BACKUPS = 1


def _setup_logger() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [vault-pull] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    lg = logging.getLogger("ohisell_vault_pull")
    lg.setLevel(logging.INFO)
    lg.handlers.clear()
    lg.addHandler(handler)
    lg.addHandler(logging.StreamHandler())
    return lg


logger = _setup_logger()


def _rsync_pull() -> bool:
    """VM 볼트 → 로컬 볼트 순방향 pull(--delete 없이 = 파괴적 삭제 안 함). 성공 시 True."""
    LOCAL_VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync", "-az",
        "--exclude", ".*.tmp",  # 서버측 원자적 쓰기 임시파일 제외
        "-e", "ssh -o ConnectTimeout=15 -o BatchMode=yes",
        f"{VM_HOST}:{VM_VAULT_PATH}/",
        f"{LOCAL_VAULT_ROOT}/",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:  # noqa: BLE001 — 네트워크·ssh 실패는 다음 주기 재시도(크래시 아님)
        logger.error("rsync 실행 실패: %s", e)
        return False
    if result.returncode != 0:
        logger.warning("rsync 실패 exit=%d stderr=%s", result.returncode, result.stderr.strip())
        return False
    return True


def _materialize_local() -> None:
    """로컬 볼트를 brctl download로 실체화(iCloud dataless evict 자가치유).

    macOS 저장공간 최적화가 iCloud 파일을 evict하면 일부 읽기/쓰기 경로가 EDEADLK로 거부되어
    rsync의 로컬 비교가 막힌다(AI_office 2026-07-15~17 볼트 대량 evict 실사고 원인). 디렉토리째
    download를 요청한다(개별 파일 순회 없이 재귀 실체화)."""
    try:
        subprocess.run(
            ["/usr/bin/brctl", "download", str(LOCAL_VAULT_ROOT)],
            check=False, capture_output=True, timeout=60,
        )
    except Exception as e:  # noqa: BLE001 — download 실패해도 재시도는 계속
        logger.warning("brctl download 실패(재시도는 계속): %s", e)


def main() -> None:
    if _rsync_pull():
        logger.info("순방향 pull 성공: %s → %s", VM_VAULT_PATH, LOCAL_VAULT_ROOT)
        return
    # 1회 자가치유 재시도 — dataless 파일 실체화 후 rsync 재실행(AI_office 패턴 축소 이식).
    logger.info("rsync 1차 실패 — brctl download로 실체화 후 1회 재시도")
    _materialize_local()
    if _rsync_pull():
        logger.info("자가치유 재시도 성공")
        return
    logger.error("rsync 재시도도 실패 — 다음 주기(15분) 재시도")
    sys.exit(0)  # launchd StartInterval이 다음 주기 재실행(비정상 종료로 스팸 로그 안 남김)


if __name__ == "__main__":
    main()
