#!/usr/bin/env bash
# capture_collection_evidence.sh — 수집 안정화 S1의 라이브 증거를 «파일로» 남긴다.
#
# ★왜 스크립트인가(2026-08-19 완료 QA 교훈): 그때 합격기준이 「라이브 화면」이었는데
#   스크린샷을 파일로 남기지 않아 독립 검증이 불가능했고 판정이 부분달성으로 떨어졌다.
#   증거는 «봤다»가 아니라 «남았다»여야 한다 — 그래서 관측을 한 명령으로 굳힌다.
#
# 읽기 전용이다. 상태를 바꾸는 명령은 하나도 없다(QA가 이 스크립트를 그대로 돌려도 안전).
#
# 사용: scripts/capture_collection_evidence.sh <라벨>      예) before-deploy / after-deploy
#       출력: docs/contracts/evidence_collection_stability_s1/<라벨>_<UTC타임스탬프>.txt
set -uo pipefail

LABEL="${1:-snapshot}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT/docs/contracts/evidence_collection_stability_s1"
mkdir -p "$OUT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_DIR/${LABEL}_${TS}.txt"

HOST="${SAFE_DEPLOY_HOST:-sellc.ohitech.co.kr}"
REMOTE="${SAFE_DEPLOY_REMOTE:-/home/ubuntu/ohisell}"
# prod 앱 포트 — pm2 프로세스 이름이 `ohisell-backend-8011`이다(2026-08-22 실측).
# nginx를 거치면 Basic Auth에 막히므로 **원격 루프백으로 직접** 친다(읽기 전용 GET).
APP_PORT="${OHISELL_APP_PORT:-8011}"

{
  echo "════ 수집 안정화 S1 라이브 증거 — $LABEL ════"
  echo "수집 시각(UTC): $TS"
  echo "수집 시각(KST): $(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST')"
  echo "로컬 HEAD     : $(git -C "$ROOT" rev-parse --short HEAD) ($(git -C "$ROOT" branch --show-current))"
  echo

  echo "── ③ CDP 로그인 탭 실존 (page 타겟 ≥1이어야 합격) ──"
  for P in 9222 9223 9224 9225; do
    python3 - "$P" <<'PY'
import json, sys, urllib.request
p = sys.argv[1]
try:
    d = json.load(urllib.request.urlopen(f"http://127.0.0.1:{p}/json/list", timeout=3))
    pages = [t.get("url", "")[:120] for t in d if t.get("type") == "page"]
    print(f"  CDP {p}: page {len(pages)}개")
    for u in pages:
        print(f"      {u}")
except Exception as e:
    print(f"  CDP {p}: 접속 불가 ({type(e).__name__})")
PY
  done
  echo

  echo "── ⑦ repo ↔ Mac 가동본 대조 (6개 전부 «차이 없음»이어야 합격) ──"
  for F in wing_browser_fetcher rocket_supplier_fetcher ad_cost_browser_fetcher \
           ohitech_ad_fetcher scheduler_watchdog_poll promo_file_fetcher; do
    if diff -q "$ROOT/tools/$F.py" "$HOME/.ohisell/tools/$F.py" >/dev/null 2>&1; then
      echo "  차이없음  $F.py"
    else
      N=$(diff "$ROOT/tools/$F.py" "$HOME/.ohisell/tools/$F.py" 2>/dev/null | grep -c '^[<>]')
      echo "  차이있음  $F.py  (${N}줄)"
    fi
  done
  echo

  echo "── launchd 데몬 ──"
  launchctl list 2>/dev/null | grep -E "ohisell" || echo "  (없음)"
  echo

  echo "── prod: 새 컬럼이 갔는가 / alembic / 코드 반영 ──"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" \
    "cd $REMOTE/backend && echo -n '  컬럼: '; sqlite3 ohisell.db 'PRAGMA table_info(coupang_wing_cookie);' | cut -d'|' -f2 | tr '\n' ' '; echo; \
     echo -n '  alembic: '; .venv/bin/alembic current 2>/dev/null | tail -1; \
     echo -n '  refresh_contract에 last_error_kind: '; grep -c last_error_kind app/services/coupang/refresh_contract.py; \
     echo -n '  collection_status에 needs_login: '; grep -c needs_login app/services/coupang/collection_status.py" \
    2>&1 | sed 's/^/  /'
  echo

  echo "── prod: 상태행 (①②④의 원장) ──"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" \
    "cd $REMOTE/backend && sqlite3 -header -column ohisell.db \
     \"SELECT account_key, status, last_error_kind, substr(coalesce(last_error,''),1,42) AS err, \
       last_error_at, refresh_requested_at AS req, attempt_count AS n \
       FROM coupang_wing_cookie ORDER BY account_key;\"" 2>&1 | sed 's/^/  /'
  echo

  echo "── ②⑤ collection-status 응답 (배너의 원천) ──"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" \
    "curl -s --max-time 10 http://127.0.0.1:$APP_PORT/api/coupang/ops/collection-status" 2>&1 \
    | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print('  (파싱 실패)', e); raise SystemExit
print('  as_of:', d.get('as_of'))
for s in d.get('streams', []):
    print(f\"  {s['key']:<14} {s['state']:<12} kind={s.get('last_error_kind')!s:<16} age_h={s.get('age_hours')}\")
"
  echo

  echo "── ①④ Mac 페처 로그 꼬리 (오늘분, 사건만) ──"
  # ★「RG 세션 프로브 auth」 반복은 뺀다 — 5초마다 찍혀 25줄을 통째로 채우고, 정작 봐야 할
  #   사건(로그아웃 확증 / 감지 실패 / 회복 / 자동 재개 / 트리거)을 화면 밖으로 밀어낸다.
  #   그 반복 자체가 «180초 블로킹»의 흔적이라, 수리 후엔 나오지 않는 것이 정상이다.
  for L in wing wing2; do
    echo "  [$L]"
    grep -E "^$(TZ=Asia/Seoul date '+%Y-%m-%d')" "$HOME/.ohisell_${L}_fetcher.log" 2>/dev/null \
      | grep -Ev "RG 세션 프로브 auth" \
      | grep -Ei "로그아웃 확증|로그인 감지|로그인 필요|로그인 회복|탭 유지|자동 재개|재개 상한|워치|트리거|갱신 요청 감지|세션 파일 없음|탭 정리" \
      | tail -30 | sed 's/^/    /'
    echo "    (참고) 오늘 「RG 세션 프로브 auth」 반복 횟수: $(grep -cE "^$(TZ=Asia/Seoul date '+%Y-%m-%d').*RG 세션 프로브 auth" "$HOME/.ohisell_${L}_fetcher.log" 2>/dev/null || echo 0)"
  done
} > "$OUT" 2>&1

echo "증거 저장: $OUT"
echo "───────────────────────────────────────"
cat "$OUT"
