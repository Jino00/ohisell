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
KEEP=7                     # 보관할 백업 **개수**(시각이 아니라 개수 — 아래 이유 참조)
TS=$(date +%Y%m%d_%H%M%S)
log() { echo "$(date -Is) $*"; }

# ── ① 보존정리 먼저 ────────────────────────────────────────────────
# 순서가 이 스크립트의 핵심이다(위 사고 참조). 정리 실패는 백업을 막지 않는다.
#
# ★왜 `-mtime +7`이 아니라 개수 기반인가(2026-08-04 실측으로 교체):
#   이 크론은 **매일 같은 시각(18:30:0x UTC)** 에 돈다. 그래서 7일 전 파일의 나이가 실행
#   시점에 **정확히 8.000일 경계**에 얹히고, `-mtime`은 소수부를 버리므로 삭제가 되기도
#   안 되기도 한다(실측: 08-03 18:31 크론에서 8일 1분 된 07-26 파일이 안 지워졌다).
#   결과는 '보존 7일'이라 적어놓고 실제로는 8개가 남는 조용한 어긋남이다.
#   개수 기반은 경계가 없고, 하루에 여러 번 돌아도 **상한이 보장**된다. 이 스크립트의 목적이
#   '디스크를 묶는 것'이므로 시각보다 개수가 목적에 직접 대응한다.
#
# ★KEEP-1로 줄이는 이유: 정리를 백업 **전에** 하므로, 지금 KEEP-1개로 만들어야 이 실행이
#   하나를 더한 뒤 정확히 KEEP개가 된다. 백업이 실패하면 한 개 덜 남지만(그래도 6개),
#   그 대가로 '백업이 디스크를 넘치게 하는 일은 없다'가 보장된다 — 사고의 교훈이 그것이다.
_trim() {  # $1=글롭 패턴, $2=남길 개수
  local keep="$2" old
  ls -1t $1 2>/dev/null | tail -n +$((keep + 1)) | while IFS= read -r old; do
    rm -f -- "$old" && log "정리: $(basename "$old")"
  done
}
_trim "$B/ohisell_daily_*.db.gz" $((KEEP - 1))
# 구 비압축(.db) 잔재는 전환기 산물이라 남기지 않는다(같은 날짜의 .gz가 이미 정본).
_trim "$B/ohisell_daily_*.db" 0

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
KEPT=$(ls -1 "$B"/ohisell_daily_*.db.gz 2>/dev/null | wc -l)
log "ok: $(basename "$TMP.gz") ${TABLES}테이블 $((DB_BYTES/1048576))MB→$((GZ_BYTES/1048576))MB 보관 ${KEPT}/${KEEP}개"
