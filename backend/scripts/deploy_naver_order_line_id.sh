#!/usr/bin/env bash
# prod 배포 스크립트 — 네이버 주문 productOrderId grain 수정 (트랙 N1·D-7)
# 운영 서버의 ohisell backend 디렉터리에서 실행. 각 단계 출력 확인 후 다음으로.
# 코드 6파일이 이미 scp로 prod에 반영된 뒤 실행할 것(STEP 0 참고).
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
echo "-- alembic current (적용 직전이면 d4f6a8c0e2b3 여야 함) --"
$ALEMBIC current
echo "-- 적용 전 네이버 주문/그룹 수 (재동기화 전 기준선) --"
$PY - <<'PYEOF'
import sqlite3
c=sqlite3.connect("ohisell.db")
nid=c.execute("SELECT id FROM channels WHERE platform='naver'").fetchone()
nid=nid[0] if nid else None
print("naver channel id:", nid)
if nid:
    print("naver orders:", c.execute("SELECT COUNT(*) FROM orders WHERE channel_id=?",(nid,)).fetchone()[0])
PYEOF

echo "════════ STEP 3. 마이그레이션 적용 (컬럼+백필+제약교체) ════════"
$ALEMBIC upgrade head
echo "-- alembic current (f0a1b2c3d4e5 여야 함) --"
$ALEMBIC current

echo "════════ STEP 4. 마이그레이션 검증 (스키마+백필) ════════"
$PY - <<'PYEOF'
import sqlite3
c=sqlite3.connect("ohisell.db")
sql=c.execute("SELECT sql FROM sqlite_master WHERE name='orders'").fetchone()[0]
assert "platform_order_line_id" in sql, "컬럼 누락!"
assert "platform_product_id, platform_order_line_id" in sql.replace('\n',' '), "uq 4컬럼 교체 안 됨!"
print("OK: 4컬럼 uq_order_item + platform_order_line_id 컬럼 존재")
nid=c.execute("SELECT id FROM channels WHERE platform='naver'").fetchone()[0]
naver=c.execute("SELECT COUNT(*) FROM orders WHERE channel_id=?",(nid,)).fetchone()[0]
filled=c.execute("SELECT COUNT(*) FROM orders WHERE channel_id=? AND platform_order_line_id!=''",(nid,)).fetchone()[0]
nonnaver_filled=c.execute("SELECT COUNT(*) FROM orders WHERE channel_id!=? AND platform_order_line_id!=''",(nid,)).fetchone()[0]
print(f"백필: naver {filled}/{naver} 채움, 비네이버 영향행 {nonnaver_filled}(0 이어야 함)")
print(f"※ 백필 못 채운 naver 행 {naver-filled}개는 재동기화 시 sync 호환 브리지가 승격(이중적재 방지)")
PYEOF

echo "════════ STEP 5. 서비스 재시작 ════════"
echo ">>> 평소 쓰는 재시작 명령을 여기서 실행하세요 (예: sudo systemctl restart ohisell  또는  pm2 restart ...)"
echo ">>> 재시작 후 Enter 를 누르면 STEP 6 검증으로 진행합니다."
read -r _

echo "════════ STEP 6. 네이버 주문 재동기화 (롤링 7일) ════════"
NID=$($PY -c "import sqlite3;print(sqlite3.connect('ohisell.db').execute(\"SELECT id FROM channels WHERE platform='naver'\").fetchone()[0])")
echo "naver channel id = $NID"
echo "-- 재동기화 호출 (기본 7일 창) --"
curl -s -X POST "http://localhost:8000/api/sync/channel/${NID}" -H "Content-Type: application/json" -d '{}' ; echo
echo "(더 넓게 복원하려면: -d '{\"date_from\":\"2026-05-05\",\"date_to\":\"2026-06-04\"}')"

echo "════════ STEP 7. 라이브 검증 (원칙 22) ════════"
$PY - <<'PYEOF'
import sqlite3
c=sqlite3.connect("ohisell.db")
nid=c.execute("SELECT id FROM channels WHERE platform='naver'").fetchone()[0]
# 분할 라인이 실제로 2행으로 보존됐는지 (같은 order+product에 line_id 2개 이상)
splits=c.execute("""
 SELECT order_number, platform_product_id, COUNT(*) n
 FROM orders WHERE channel_id=? AND platform_order_line_id!=''
 GROUP BY order_number, platform_product_id HAVING n>1
""",(nid,)).fetchall()
print(f"분할 보존된 (주문,상품) 그룹: {len(splits)}개 (수정 전엔 0이었음)")
for s in splits[:5]: print("  ", s)
# 재동기화 후 네이버에 빈 line_id 잔존 (7일 창 밖 historical은 남을 수 있음 — 정상)
empty=c.execute("SELECT COUNT(*) FROM orders WHERE channel_id=? AND platform_order_line_id=''",(nid,)).fetchone()[0]
print(f"네이버 빈 line_id 잔존: {empty} (재동기화 7일 창 안은 0, 그 밖 historical은 잔존 가능)")
PYEOF
echo "※ sales-summary 카드 매출/이익도 패널에서 눈으로 확인 (분할 1.4% 그룹 매출 증가 반영)"
echo "════════ 완료 ════════"
