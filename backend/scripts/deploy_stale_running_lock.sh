#!/usr/bin/env bash
# prod 배포 스크립트 — stale 'running' SyncLog 영구차단 버그 수정 (task_f1f36f02)
# 운영 서버의 ohisell backend 디렉터리에서 실행. 각 단계 출력 확인 후 다음으로.
# 코드 1파일(app/services/sync_service.py)이 이미 scp/pull로 prod에 반영된 뒤 실행할 것.
set -euo pipefail

# ── 환경 (서버에 맞게 확인) ─────────────────────────────────────
APP_DIR="${APP_DIR:-$HOME/ohisell/backend}"   # ← prod backend 경로 (다르면 수정)
cd "$APP_DIR"
DB_FILE="${DB_FILE:-ohisell.db}"              # ← prod DB 파일명 (다르면 수정)
ALEMBIC="${ALEMBIC:-.venv/bin/alembic}"
PY="${PY:-.venv/bin/python}"

echo "════════ STEP 1. prod DB 백업 ════════"
cp "$DB_FILE" "${DB_FILE}.bak.$(date +%s)"
ls -la "${DB_FILE}.bak."* | tail -1

echo "════════ STEP 2. 마이그레이션 전 상태 확인 ════════"
echo "-- alembic current (적용 직전이면 i3j4k5l6m7n8 여야 함) --"
$ALEMBIC current
echo "-- 적용 전 'running' SyncLog 수 (이 행들이 회수 대상) --"
$PY - <<'PYEOF'
import sqlite3
c = sqlite3.connect("ohisell.db")
rows = c.execute("SELECT channel_id, COUNT(*) FROM sync_log WHERE status='running' GROUP BY channel_id").fetchall()
total = c.execute("SELECT COUNT(*) FROM sync_log WHERE status='running'").fetchone()[0]
print(f"running 행 총 {total}건, 채널별: {rows or '(없음)'}")
PYEOF

echo "════════ STEP 3. 마이그레이션 적용 (잔존 running 일괄 회수) ════════"
$ALEMBIC upgrade head
echo "-- alembic current (j4k5l6m7n8o9 여야 함) --"
$ALEMBIC current

echo "════════ STEP 4. 검증 (running 0건 + 회수 라벨 확인) ════════"
$PY - <<'PYEOF'
import sqlite3
c = sqlite3.connect("ohisell.db")
running = c.execute("SELECT COUNT(*) FROM sync_log WHERE status='running'").fetchone()[0]
assert running == 0, f"여전히 running 행 {running}건 — 마이그레이션 실패!"
reclaimed = c.execute(
    "SELECT COUNT(*) FROM sync_log WHERE error_message LIKE '%stale running cleared on deploy%'"
).fetchone()[0]
print(f"OK: running 0건, 이번 배포로 회수된 행 {reclaimed}건")
PYEOF

echo "════════ STEP 5. 앱 재시작 후 라이브 확인 (수동) ════════"
echo "  앱 재시작 → 막혀 있던 채널(예: 쿠팡 오하이테크 channel_id=2)에 동기화 1회 호출:"
echo "    POST /api/sync/channel/2   (또는 UI 동기화 버튼)"
echo "  '이미 동기화가 진행 중입니다' 없이 정상 동작하면 영구차단 해소 확인."
echo "  이후 정상 동기화의 started_at은 KST로 기록됨(completed_at과 단위 일치)."
