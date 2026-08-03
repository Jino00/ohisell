#!/bin/bash
# ohisell DB 일일 백업 — gzip, 7일 보관 (crontab: 30 18 * * * UTC = 03:30 KST)
#
# ★존재 이유가 된 사고(2026-08-03 03:32 KST, ENOSPC):
#   구버전은 이랬다 —
#       set -e
#       sqlite3 ... .backup ...          # ①백업
#       find ... -mtime +14 -delete      # ②보존정리
#   1.4GB 백업을 쓰다 디스크가 꽉 차 ①이 실패하면 `set -e`가 즉시 종료시켜 **②가 아예 안 돈다**.
#   공간을 비워야 할 바로 그날 비우는 동작을 건너뛰는 자기모순이다. 실제로:
#     · 03:32 ENOSPC → 서버에서 쓰기가 필요한 모든 프로세스가 마비(PM2 스택 포함)
#     · 03:32~07:10 백엔드 로그 완전 공백(3시간 40분)
#     · 05:20~07:00 예정 자동수집 **12개가 통째로 유실**(catch-up 목록 밖이라 복구도 없음)
#     · 0.06GB짜리 잘린 백업 파일이 남음(테이블 0개 — 백업 구실을 못 하는 껍데기)
#   광고비 잡은 '어제 하루치만' 쓰므로, 사람이 눈치채지 못했으면 그날 광고비가 손익에서
#   영구히 비었을 것이다 — 2026-08-03에 발견한 488만원 누락과 같은 소멸 방식이다.
#
# ★그래서 바꾼 네 가지:
#   ①보존정리를 **먼저** 한다(실패해도 백업을 막지 않게 `|| true`).
#   ②시작 전 **여유 공간을 확인**하고 모자라면 백업을 아예 시도하지 않는다 —
#     디스크를 꽉 채워 서버 전체를 마비시키느니 백업 하루를 건너뛰는 게 낫다.
#   ③백업 후 **유효성 검증**(테이블 수 > 0). 껍데기를 성공으로 치지 않는다.
#   ④실패 시 **부분 파일 제거**. 잘린 파일이 다음 날 보존정리를 헷갈리게 두지 않는다.
#
# ★`set -e`를 쓰지 않는 이유: 이 스크립트의 요구사항이 "어느 단계가 실패해도 정리는 돈다"이므로
#   즉시종료는 오히려 해롭다. 대신 각 단계를 명시적으로 검사하고 exit code로 알린다.
set -uo pipefail

D=/home/ubuntu/ohisell/backend
B="$D/backups"
DB="$D/ohisell.db"
RETENTION_DAYS=7
TS=$(date +%Y%m%d_%H%M%S)
log() { echo "$(date -Is) $*"; }

# ── ① 보존정리 먼저 ────────────────────────────────────────────────
# 순서가 이 스크립트의 핵심이다(위 사고 참조). 정리 실패는 백업을 막지 않는다.
# 구 비압축(.db) 잔재도 같은 규칙으로 정리한다 — 전환기 혼재를 남기지 않기 위함.
find "$B" -maxdepth 1 -name 'ohisell_daily_*.db.gz' -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true
find "$B" -maxdepth 1 -name 'ohisell_daily_*.db'    -mtime +${RETENTION_DAYS} -delete 2>/dev/null || true

# ── ② 여유 공간 사전 확인 ──────────────────────────────────────────
# .backup은 원본 크기만큼 쓰고, 이어지는 gzip은 잠깐 원본+압축본이 공존한다 → 원본의 1.3배를
# 최소선으로 본다. 부족하면 **백업을 건너뛴다**: 백업 하루 결손 < 디스크 포화로 인한 전체 마비.
DB_BYTES=$(stat -c %s "$DB" 2>/dev/null || echo 0)
if [ "$DB_BYTES" -eq 0 ]; then
  log "ABORT: DB를 읽을 수 없음: $DB"
  exit 1
fi
NEED=$(( DB_BYTES * 13 / 10 ))
# `df -P --output=...`는 GNU df에서 상호배타(실측: "options -P and --output are mutually
# exclusive") — -B1로 바이트를 직접 받는다. 1K 블록 곱셈도 함께 사라진다.
AVAIL=$(df -B1 --output=avail "$B" 2>/dev/null | tail -1)
if [ -z "${AVAIL:-}" ] || [ "$AVAIL" -lt "$NEED" ]; then
  log "SKIP: 여유 공간 부족 — 필요 $((NEED/1048576))MB / 여유 $((AVAIL/1048576))MB. 백업 건너뜀(디스크 포화 방지)."
  exit 1
fi

# ── ③ 백업 → 유효성 검증 → 압축 ────────────────────────────────────
TMP="$B/ohisell_daily_${TS}.db"
if ! sqlite3 "$DB" ".backup '$TMP'"; then
  rm -f "$TMP"
  log "FAIL: sqlite3 .backup 실패 — 부분 파일 제거함"
  exit 1
fi

# 껍데기 검출: 2026-08-03 사고의 잘린 파일은 integrity_check가 'ok'였지만 테이블이 0개였다.
# integrity_check만으로는 못 잡으므로 테이블 수를 본다.
TABLES=$(sqlite3 "$TMP" "SELECT count(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo 0)
if [ "${TABLES:-0}" -lt 1 ]; then
  rm -f "$TMP"
  log "FAIL: 백업이 비어 있음(테이블 ${TABLES}개) — 제거함"
  exit 1
fi

if ! gzip -f "$TMP"; then
  rm -f "$TMP" "$TMP.gz"
  log "FAIL: gzip 실패 — 부분 파일 제거함"
  exit 1
fi

GZ_BYTES=$(stat -c %s "$TMP.gz" 2>/dev/null || echo 0)
log "ok: $(basename "$TMP.gz") ${TABLES}테이블 $((DB_BYTES/1048576))MB→$((GZ_BYTES/1048576))MB 보존 ${RETENTION_DAYS}일"
