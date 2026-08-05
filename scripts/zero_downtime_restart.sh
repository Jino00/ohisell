#!/usr/bin/env bash
# zero_downtime_restart.sh — prod 백엔드 무중단 재시작(블루-그린)
#
# ★존재 이유(2026-08-05 실측): 3일간 prod 백엔드가 스스로 죽은 적은 0회다. 그런데 화면과
# Mac 페처는 502/연결거부를 계속 봤다. 정체는 **배포 재시작**이었다 — pm2 restart가
# 콜드부팅 ~50초의 공백을 만들고(19:06:16 종료 → 19:07:05 기동, nginx 에러로그와 초 단위
# 일치), 그 창에 들어온 요청이 전부 502가 됐다. 재시작 횟수: 08-03 34회 / 08-04 19회 /
# 08-05 8회. 즉 하루 종일 50초짜리 구멍이 수십 개 뚫려 있었다.
#   그 창에서 갱신 버튼의 request-refresh POST가 유실돼 "버튼을 눌러도 아무 일이 없다"가
#   됐고(2026-08-05 Jino 보고), Mac 데몬의 폴·push도 같은 이유로 실패했다.
#
# ★왜 "배포를 줄이자"가 답이 아닌가: 배포가 잦은 건 개발이 활발하다는 뜻이라 죽일 수 없는
# 원인이다. 그러면 **배포가 아무리 잦아도 무해하게** 만드는 것이 유일한 근본 수리다.
#
# 동작(전부 원격에서):
#   ①대기 포트에 새 프로세스 기동 → ②/health 200 확인 → ③nginx upstream 전환(reload)
#   → ④구 프로세스로 가는 진행 중 요청이 끝날 때까지 드레인 → ⑤구 프로세스 종료
#   → ⑥신 프로세스가 스케줄러 리더로 승격했는지 확인 → ⑦pm2 save
#   실패하면 어느 단계든 **구 프로세스를 살려둔 채** 중단한다(그게 롤백이다).
#
# ★스케줄러 이중 발화 방지: ①~⑤ 사이 두 프로세스가 겹친다. app/services/scheduler_leader.py
# 의 파일 락으로 **리더 1개만** 스케줄러를 돌린다. 신 프로세스는 standby로 떴다가 구
# 프로세스가 죽으면 승격한다. 이 스크립트는 승격을 /health로 실측하고서야 성공을 선언한다.
#
# 사용:
#   scripts/zero_downtime_restart.sh              # 무중단 재시작
#   scripts/zero_downtime_restart.sh --bootstrap  # 최초 1회: nginx를 upstream 구조로 전환
#   scripts/zero_downtime_restart.sh --status     # 현재 활성 포트·프로세스 확인(변경 없음)
set -euo pipefail

HOST="${SAFE_DEPLOY_HOST:-sellc.ohitech.co.kr}"
REMOTE_REPO="${SAFE_DEPLOY_REMOTE:-/home/ubuntu/ohisell}"
UPSTREAM_CONF="/etc/nginx/conf.d/ohisell-upstream.conf"
SITE_CONF="/etc/nginx/sites-available/sellc.ohitech.co.kr"
UPSTREAM_NAME="ohisell_backend"
BLUE_PORT=8001
GREEN_PORT=8011
HEALTH_TIMEOUT="${ZDR_HEALTH_TIMEOUT:-180}"   # 신 프로세스가 트래픽 받을 준비까지 대기 상한
DRAIN_TIMEOUT="${ZDR_DRAIN_TIMEOUT:-120}"     # 구 프로세스 진행 중 요청 대기 상한
LEADER_TIMEOUT="${ZDR_LEADER_TIMEOUT:-60}"    # 스케줄러 승격 대기 상한

BOOTSTRAP=0; STATUS_ONLY=0
for a in "$@"; do
  case "$a" in
    --bootstrap) BOOTSTRAP=1 ;;
    --status) STATUS_ONLY=1 ;;
    -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
    *) echo "알 수 없는 인자: $a" >&2; exit 2 ;;
  esac
done

sshx() { ssh -o BatchMode=yes "$HOST" "$@"; }
fail() { echo "❌ ABORT: $1" >&2; exit 1; }

# ── 활성 포트 판정 ───────────────────────────────────────────────────
# upstream conf가 정본이다. 없으면 아직 부트스트랩 전 = 레거시 단일 프로세스(8001).
active_port() {
  sshx "grep -oE 'server 127\\.0\\.0\\.1:[0-9]+' '$UPSTREAM_CONF' 2>/dev/null | grep -oE '[0-9]+$' | head -1" || true
}

pm2_name_for() { echo "ohisell-backend-$1"; }

# 레거시 프로세스 이름(포트 접미사 없음) — 부트스트랩 이전에 돌던 것.
LEGACY_NAME="ohisell-backend"

if [ "$STATUS_ONLY" = 1 ]; then
  AP=$(active_port)
  echo "활성 포트: ${AP:-'(upstream conf 없음 — 레거시 8001로 간주)'}"
  sshx "pm2 list | grep -E 'ohisell-backend' || echo '(pm2에 ohisell-backend 프로세스 없음)'"
  sshx "curl -s --max-time 5 http://127.0.0.1:${AP:-8001}/health || echo '(health 응답 없음)'"
  exit 0
fi

# ── 부트스트랩: nginx를 upstream 구조로 (최초 1회) ────────────────────
if [ "$BOOTSTRAP" = 1 ]; then
  echo "▶ 부트스트랩: nginx를 upstream 구조로 전환합니다(현재 트래픽 무중단)."
  CUR=$(active_port)
  if [ -n "$CUR" ]; then
    echo "  이미 부트스트랩됨(활성 포트 $CUR) — 건너뜁니다."
    exit 0
  fi
  # 현재 sites-available이 localhost:8001 직결인지 확인하고, 그 포트를 그대로 upstream으로.
  sshx "grep -q 'proxy_pass http://localhost:$BLUE_PORT/api/;' '$SITE_CONF'" \
    || fail "site conf가 예상 형태가 아닙니다(proxy_pass http://localhost:$BLUE_PORT/api/;). 수동 확인 필요."

  STAMP=$(date -u +%Y%m%d-%H%M%S)
  sshx "sudo cp '$SITE_CONF' '$SITE_CONF.bak-zdr-$STAMP'" || fail "site conf 백업 실패"
  sshx "printf '%s\n' 'upstream $UPSTREAM_NAME {' '    server 127.0.0.1:$BLUE_PORT;' '}' | sudo tee '$UPSTREAM_CONF' >/dev/null" \
    || fail "upstream conf 작성 실패"
  sshx "sudo sed -i 's|proxy_pass http://localhost:$BLUE_PORT/api/;|proxy_pass http://$UPSTREAM_NAME/api/;|' '$SITE_CONF'" \
    || fail "site conf 치환 실패"
  if ! sshx "sudo nginx -t" 2>&1 | tail -2; then
    sshx "sudo cp '$SITE_CONF.bak-zdr-$STAMP' '$SITE_CONF'; sudo rm -f '$UPSTREAM_CONF'"
    fail "nginx -t 실패 — 원복했습니다."
  fi
  sshx "sudo systemctl reload nginx" || fail "nginx reload 실패"
  echo "  ✅ upstream 구조 전환 완료(백업: $SITE_CONF.bak-zdr-$STAMP)"
  echo "  ℹ️  프로세스는 아직 레거시 이름($LEGACY_NAME, 포트 $BLUE_PORT)입니다 — 다음 무중단 재시작에서 정리됩니다."
  exit 0
fi

# ── 무중단 재시작 ────────────────────────────────────────────────────
CUR_PORT=$(active_port)
[ -n "$CUR_PORT" ] || fail "upstream conf가 없습니다. 먼저 --bootstrap 을 1회 실행하세요."

if [ "$CUR_PORT" = "$BLUE_PORT" ]; then TARGET_PORT=$GREEN_PORT; else TARGET_PORT=$BLUE_PORT; fi
TARGET_NAME=$(pm2_name_for "$TARGET_PORT")
CUR_NAME=$(pm2_name_for "$CUR_PORT")

# 현재 활성 프로세스가 레거시 이름으로 돌고 있는지(부트스트랩 직후 1회) 확인.
if ! sshx "pm2 describe '$CUR_NAME' >/dev/null 2>&1"; then
  if sshx "pm2 describe '$LEGACY_NAME' >/dev/null 2>&1"; then
    CUR_NAME="$LEGACY_NAME"
    echo "ℹ️  활성 프로세스가 레거시 이름($LEGACY_NAME)입니다 — 이번 전환에서 정리합니다."
  else
    fail "활성 포트 $CUR_PORT 를 서빙하는 pm2 프로세스를 찾지 못했습니다($CUR_NAME / $LEGACY_NAME 둘 다 없음)."
  fi
fi

echo "▶ 무중단 재시작: $CUR_NAME(:$CUR_PORT) → $TARGET_NAME(:$TARGET_PORT)"

# (1) 이전 실패로 남은 대기 프로세스가 있으면 정리하고 새로 띄운다.
sshx "pm2 delete '$TARGET_NAME' >/dev/null 2>&1 || true"
sshx "cd '$REMOTE_REPO/backend' && pm2 start '$REMOTE_REPO/backend/.venv/bin/python3' \
        --name '$TARGET_NAME' --interpreter none --cwd '$REMOTE_REPO/backend' -- \
        -m uvicorn app.main:app --host 0.0.0.0 --port $TARGET_PORT --workers 1 --proxy-headers" \
  >/dev/null || fail "신 프로세스 기동 실패($TARGET_NAME)"
echo "  ① 신 프로세스 기동됨 — 헬스체크 대기(최대 ${HEALTH_TIMEOUT}s)"

# (2) 헬스체크 — 여기서 실패하면 구 프로세스는 그대로 서빙 중이므로 사용자 영향 0.
if ! sshx "for i in \$(seq 1 $HEALTH_TIMEOUT); do
             if curl -fsS --max-time 3 http://127.0.0.1:$TARGET_PORT/health >/dev/null 2>&1; then exit 0; fi
             sleep 1
           done; exit 1"; then
  echo "  ✖ 신 프로세스가 ${HEALTH_TIMEOUT}s 내에 준비되지 않았습니다 — 부팅 로그:" >&2
  sshx "pm2 logs '$TARGET_NAME' --lines 30 --nostream 2>/dev/null | tail -30" >&2 || true
  sshx "pm2 delete '$TARGET_NAME' >/dev/null 2>&1 || true"
  fail "헬스체크 실패 — 전환하지 않았습니다(구 프로세스 계속 서빙 중, 사용자 영향 없음)."
fi
echo "  ② 헬스체크 통과(:$TARGET_PORT)"

# (3) nginx 전환 — reload는 무중단이다(구 워커가 진행 중 요청을 마치고 물러난다).
STAMP=$(date -u +%Y%m%d-%H%M%S)
sshx "sudo cp '$UPSTREAM_CONF' '/tmp/ohisell-upstream.bak-$STAMP'"
sshx "printf '%s\n' 'upstream $UPSTREAM_NAME {' '    server 127.0.0.1:$TARGET_PORT;' '}' | sudo tee '$UPSTREAM_CONF' >/dev/null"
if ! sshx "sudo nginx -t >/dev/null 2>&1"; then
  sshx "sudo cp '/tmp/ohisell-upstream.bak-$STAMP' '$UPSTREAM_CONF'; sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx"
  sshx "pm2 delete '$TARGET_NAME' >/dev/null 2>&1 || true"
  fail "nginx -t 실패 — upstream을 원복했습니다(구 프로세스 계속 서빙)."
fi
sshx "sudo systemctl reload nginx" || fail "nginx reload 실패 — 수동 확인 필요(upstream은 이미 :$TARGET_PORT)."
echo "  ③ 트래픽 전환 완료(:$CUR_PORT → :$TARGET_PORT)"

# (4) 드레인 — 구 포트로 진행 중이던 요청이 끝날 때까지 기다린다.
#     ★고정 sleep이 아니라 실제 연결 수로 판정한다: /api/sync/realtime 같은 장기 요청이
#     30~60초씩 걸리는데(proxy_read_timeout 600s), 짧은 sleep 후 죽이면 무중단이 아니라
#     "짧아진 다운타임"이 된다.
echo "  ④ 구 프로세스 드레인 대기(최대 ${DRAIN_TIMEOUT}s)"
sshx "for i in \$(seq 1 $DRAIN_TIMEOUT); do
        N=\$(ss -Htn state established \"( sport = :$CUR_PORT )\" 2>/dev/null | wc -l)
        if [ \"\$N\" -eq 0 ]; then echo \"    드레인 완료(\${i}s)\"; exit 0; fi
        sleep 1
      done; echo '    ⚠️ 드레인 타임아웃 — 남은 연결이 있는 채로 종료합니다'; exit 0"

# (5) 구 프로세스 종료 → 커널이 스케줄러 락을 해제한다.
sshx "pm2 delete '$CUR_NAME' >/dev/null 2>&1 || true"
echo "  ⑤ 구 프로세스 종료됨($CUR_NAME)"

# (6) 스케줄러 리더 승격 확인 — "됐다"를 로그가 아니라 API로 판정한다.
if ! sshx "for i in \$(seq 1 $LEADER_TIMEOUT); do
             if curl -fsS --max-time 3 http://127.0.0.1:$TARGET_PORT/health 2>/dev/null | grep -q '\"scheduler_leader\":true'; then exit 0; fi
             sleep 1
           done; exit 1"; then
  echo "  ⚠️ 경고: ${LEADER_TIMEOUT}s 내에 스케줄러 리더 승격이 확인되지 않았습니다." >&2
  echo "     HTTP는 정상 서빙 중이나 크론이 안 돌 수 있습니다 — 즉시 확인:" >&2
  echo "     ssh $HOST \"curl -s http://127.0.0.1:$TARGET_PORT/health; pm2 logs $TARGET_NAME --lines 40 --nostream\"" >&2
  exit 1
fi
echo "  ⑥ 스케줄러 리더 승격 확인"

# (7) 재부팅 후에도 살아남게 저장 + 매니페스트 기록
sshx "pm2 save >/dev/null 2>&1 || true"
sshx "printf '%s\n' '{\"ts\":\"$(date -u +%FT%TZ)\",\"kind\":\"zero-downtime-restart\",\"from_port\":$CUR_PORT,\"to_port\":$TARGET_PORT}' >> '$REMOTE_REPO/deploy-manifest.jsonl'"

echo "✅ 무중단 재시작 완료 — 활성 :$TARGET_PORT ($TARGET_NAME), 다운타임 0초"
