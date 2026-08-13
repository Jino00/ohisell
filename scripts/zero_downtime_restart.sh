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
VHOST="${SAFE_DEPLOY_VHOST:-sellc.ohitech.co.kr}"   # 전환 검증을 사용자와 같은 경로로 던질 주소
UPSTREAM_CONF="/etc/nginx/conf.d/ohisell-upstream.conf"
SITE_CONF="/etc/nginx/sites-available/sellc.ohitech.co.kr"
UPSTREAM_NAME="ohisell_backend"
BLUE_PORT=8001
GREEN_PORT=8011
# ★단위는 '초'가 아니라 **반복 횟수**다(P2). 각 반복에 curl --max-time 3이 붙으므로
#   최악의 경우 실제 경과는 이 값의 최대 4배까지 늘어난다. 이름을 헷갈리지 않게 _TRIES로 둔다.
HEALTH_TRIES="${ZDR_HEALTH_TRIES:-${ZDR_HEALTH_TIMEOUT:-180}}"   # 신 프로세스 준비 대기(회)
DRAIN_TIMEOUT="${ZDR_DRAIN_TIMEOUT:-120}"                        # 드레인은 sleep 1뿐이라 ≈초
LEADER_TRIES="${ZDR_LEADER_TRIES:-${ZDR_LEADER_TIMEOUT:-60}}"    # 스케줄러 승격 대기(회)

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

# ── 재시작 락 (P1-4) ─────────────────────────────────────────────────
# ★safe_deploy.sh는 파일 배포 구간에만 락을 건다. 이 스크립트는 단독 실행이 정식 사용법이라
# 자체 락이 없으면 병행 세션이 서로의 라이브 프로세스를 지운다: 세션 A가 ③(전환)까지 마치고
# ④ 드레인 중일 때 B가 시작하면, B는 전환 전 값으로 CUR/TARGET을 계산해 **방금 라이브가 된
# 프로세스를 pm2 delete** 한다 → nginx는 있는데 아무도 안 듣는다. 이 PR이 없애려는 502를
# 더 나쁜 형태로 재생산하는 것이다(D-NAO-49가 파일 배포에 락을 건 것과 같은 이유).
# safe_deploy.sh가 이미 잡은 경우를 위해 별도 이름을 쓰되, 재진입은 허용하지 않는다.
RESTART_LOCK="$REMOTE_REPO/.restart-lock"
_lock_acquired=0
release_restart_lock() {
  [ "$_lock_acquired" = 1 ] || return 0
  sshx "rm -rf '$RESTART_LOCK'" 2>/dev/null || true
  _lock_acquired=0
}
acquire_restart_lock() {
  if ! sshx "mkdir '$RESTART_LOCK' 2>/dev/null"; then
    echo "  현재 락 보유자:" >&2
    sshx "cat '$RESTART_LOCK/owner' 2>/dev/null || echo '(정보 없음)'" >&2 || true
    fail "다른 세션이 재시작 중입니다. 끝난 뒤 다시 실행하세요(죽은 락이면: ssh $HOST \"rm -rf '$RESTART_LOCK'\")."
  fi
  _lock_acquired=1
  trap release_restart_lock EXIT
  sshx "printf '%s\n' 'host=$(hostname -s) pid=$$ ts=$(date -u +%FT%TZ)' > '$RESTART_LOCK/owner'" || true
}

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
# ★락을 **활성 포트를 읽기 전에** 잡는다 — 읽고 나서 잡으면 그 사이 다른 세션이 전환을
#   끝내 CUR/TARGET이 낡은 값이 된다(P1-4가 지적한 바로 그 경합).
acquire_restart_lock

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
echo "  ① 신 프로세스 기동됨 — 헬스체크 대기(최대 ${HEALTH_TRIES}s)"

# (2) 헬스체크 — 여기서 실패하면 구 프로세스는 그대로 서빙 중이므로 사용자 영향 0.
if ! sshx "for i in \$(seq 1 $HEALTH_TRIES); do
             if curl -fsS --max-time 3 http://127.0.0.1:$TARGET_PORT/health >/dev/null 2>&1; then exit 0; fi
             sleep 1
           done; exit 1"; then
  echo "  ✖ 신 프로세스가 ${HEALTH_TRIES}s 내에 준비되지 않았습니다 — 부팅 로그:" >&2
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

# ★★사용자 경로(nginx)를 실제로 통과시켜 확인한다 — 구 프로세스를 죽이기 **전에**(P1-3).
# 초판은 전 과정을 127.0.0.1:포트로만 확인했다. 그러면 sites-enabled가 심볼릭 링크가 아니라
# 복사본이거나 같은 server_name의 server 블록이 conf.d에 하나 더 있는 경우, nginx -t는
# 통과하고 reload도 0을 반환하지만 **실제 서빙 vhost는 여전히 구 포트를 가리킨다**.
# 그 상태로 ⑤에서 구 프로세스를 죽이면 전 요청이 502가 되는데 스크립트는 "다운타임 0초"를
# 출력한다 — 이 저장소의 "로그만 보고 성공으로 오인" 사고와 정확히 같은 형태다.
# 판정법: vhost를 통과한 요청이 **신 프로세스의 pid**를 답해야 한다(포트가 아니라 프로세스 동일성).
TARGET_PID=$(sshx "curl -fsS --max-time 5 http://127.0.0.1:$TARGET_PORT/health" | sed -n 's/.*\"pid\":\([0-9]*\).*/\1/p')
[ -n "$TARGET_PID" ] || fail "신 프로세스 pid를 읽지 못했습니다(/health 응답 형식 확인 필요)."

# ★프로브를 **이 기계(배포를 실행하는 곳)에서** 던진다 — 서버 안에서 Host 헤더로 찌르는
# 것보다 낫다: DNS·TLS·nginx·접근통제·업스트림 전 구간이 사용자와 똑같이 실행된다.
# (서버 로컬에서 찌르면 허용목록에 127.0.0.1이 없어 403이고, 그걸 뚫으려 허용목록을 넓히는
#  것은 이 스크립트가 건드릴 일이 아니다 — 그 목록은 2026-07-17 무인증 공개 사고의 처방이다.)
# ★자격증명(Basic Auth) — prod가 IP 허용목록에서 비밀번호로 넘어가는 중이다.
#   `~/.ohisell_prod_auth` 파일이 있으면 `user:pass` 한 줄을 읽어 프로브에 싣는다.
#   **없으면 지금과 똑같이 동작한다** — 그래야 이 커밋을 먼저 배포해 두고 나중에 nginx를
#   켜는 «순서»가 성립한다(둘을 동시에 바꾸면 되돌릴 곳이 두 곳이 된다).
#   ⚠️파일 내용은 절대 로그로 출력하지 않는다(자백 로그에도 안 남긴다).
AUTH_FILE="${OHISELL_PROD_AUTH_FILE:-$HOME/.ohisell_prod_auth}"
CURL_AUTH=()
if [ -r "$AUTH_FILE" ]; then
  CURL_AUTH=(-u "$(head -n1 "$AUTH_FILE" | tr -d '\r\n')")
fi
VHOST_PID=$(curl -fsS --max-time 10 "${CURL_AUTH[@]}" "https://$VHOST/api/health" 2>/dev/null \
            | sed -n 's/.*\"pid\":\([0-9]*\).*/\1/p') || true
if [ -z "$VHOST_PID" ]; then
  echo "  ⚠️ 공개 URL(https://$VHOST/api/health) 경유 확인을 못 했습니다." >&2
  echo "     원인 후보: 이 기계 IP가 nginx 허용목록에 없음 / **Basic Auth 자격증명 없음**" >&2
  echo "                ($AUTH_FILE 에 'user:pass' 한 줄) / 신 코드에 /api/health 없음 / 네트워크." >&2
  echo "     구 프로세스를 죽이기 전이라 사용자 영향은 없습니다. upstream 원복 후 중단합니다." >&2
  sshx "sudo cp '/tmp/ohisell-upstream.bak-$STAMP' '$UPSTREAM_CONF'; sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx"
  sshx "pm2 delete '$TARGET_NAME' >/dev/null 2>&1 || true"
  fail "vhost 경유 검증 불가 — 전환을 되돌렸습니다(구 프로세스 계속 서빙)."
fi
if [ "$VHOST_PID" != "$TARGET_PID" ]; then
  echo "  ✖ vhost가 신 프로세스를 가리키지 않습니다(vhost pid=$VHOST_PID, 신 프로세스 pid=$TARGET_PID)." >&2
  echo "     같은 server_name의 다른 server 블록이나 sites-enabled 복사본을 의심하세요." >&2
  sshx "sudo cp '/tmp/ohisell-upstream.bak-$STAMP' '$UPSTREAM_CONF'; sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx"
  sshx "pm2 delete '$TARGET_NAME' >/dev/null 2>&1 || true"
  fail "전환이 실제로 반영되지 않았습니다 — 되돌렸습니다(구 프로세스 계속 서빙)."
fi
echo "  ③ 트래픽 전환 완료·vhost 경유 확인(:$CUR_PORT → :$TARGET_PORT, pid=$TARGET_PID)"

# ★pm2 dump를 **여기서** 저장한다(P1-2). 초판은 ⑦에서만 저장해서, ⑤~⑥ 사이 어떤 실패로
# 종료해도 dump에는 구 프로세스 이름이 남았다 → 서버 재부팅/pm2 resurrect 시 **구 포트에
# 프로세스가 뜨고 nginx는 신 포트를 가리키는** 영구 502 시한폭탄이 됐다. 전환이 확정된
# 지금이 dump와 실제 상태가 일치하는 시점이다(구 프로세스 삭제 직후 한 번 더 저장한다).
sshx "pm2 save >/dev/null 2>&1 || true"

# (4) 드레인 — 구 포트로 진행 중이던 요청이 끝날 때까지 기다린다.
#     ★고정 sleep이 아니라 실제 연결 수로 판정한다: /api/sync/realtime 같은 장기 요청이
#     30~60초씩 걸리는데(proxy_read_timeout 600s), 짧은 sleep 후 죽이면 무중단이 아니라
#     "짧아진 다운타임"이 된다.
echo "  ④ 구 프로세스 드레인 대기(최대 ${DRAIN_TIMEOUT}s)"
# ★ss가 없거나 필터 문법이 안 먹으면 "연결 0"과 구분되지 않아 진행 중 요청을 끊는다(P2).
#   그래서 ss 자체의 동작을 먼저 확인하고, 못 쓰면 보수적으로 고정 대기로 떨어진다.
if ! sshx "command -v ss >/dev/null 2>&1 && ss -Htn state established '( sport = :$CUR_PORT )' >/dev/null 2>&1"; then
  echo "    ⚠️ ss로 연결 수를 셀 수 없습니다 — 보수적으로 ${DRAIN_TIMEOUT}s 고정 대기합니다." >&2
  sshx "sleep $DRAIN_TIMEOUT"
else
  sshx "for i in \$(seq 1 $DRAIN_TIMEOUT); do
          N=\$(ss -Htn state established \"( sport = :$CUR_PORT )\" 2>/dev/null | wc -l)
          if [ \"\$N\" -eq 0 ]; then echo \"    드레인 완료(\${i}s)\"; exit 0; fi
          sleep 1
        done; echo '    ⚠️ 드레인 타임아웃 — 남은 연결이 있는 채로 종료합니다'; exit 0"
fi

# (5) 구 프로세스 종료 → 커널이 스케줄러 락을 해제한다.
sshx "pm2 delete '$CUR_NAME' >/dev/null 2>&1 || true"
# ★삭제 직후 곧바로 dump를 갱신한다(P1-2): 이 뒤 어떤 실패로 종료해도 pm2 dump와 실제
#   프로세스 구성이 어긋나지 않는다. 어긋난 채 재부팅되면 구 포트에 프로세스가 뜨고
#   nginx는 신 포트를 가리켜 **영구 502**가 된다.
sshx "pm2 save >/dev/null 2>&1 || true"
echo "  ⑤ 구 프로세스 종료됨($CUR_NAME) · pm2 dump 갱신"

# (6) 스케줄러 리더 승격 확인 — "됐다"를 로그가 아니라 API로 판정한다.
if ! sshx "for i in \$(seq 1 $LEADER_TRIES); do
             if curl -fsS --max-time 3 http://127.0.0.1:$TARGET_PORT/health 2>/dev/null | grep -q '\"scheduler_leader\":true'; then exit 0; fi
             sleep 1
           done; exit 1"; then
  echo "  ⚠️ 경고: ${LEADER_TRIES}s 내에 스케줄러 리더 승격이 확인되지 않았습니다." >&2
  echo "     HTTP는 정상 서빙 중이나 크론이 안 돌 수 있습니다 — 즉시 확인:" >&2
  echo "     ssh $HOST \"curl -s http://127.0.0.1:$TARGET_PORT/health; pm2 logs $TARGET_NAME --lines 40 --nostream\"" >&2
  exit 1
fi
echo "  ⑥ 스케줄러 리더 승격 확인"

# (7) 재부팅 후에도 살아남게 저장 + 매니페스트 기록
sshx "pm2 save >/dev/null 2>&1 || true"
sshx "printf '%s\n' '{\"ts\":\"$(date -u +%FT%TZ)\",\"kind\":\"zero-downtime-restart\",\"from_port\":$CUR_PORT,\"to_port\":$TARGET_PORT}' >> '$REMOTE_REPO/deploy-manifest.jsonl'"

echo "✅ 무중단 재시작 완료 — 활성 :$TARGET_PORT ($TARGET_NAME), 다운타임 0초"
