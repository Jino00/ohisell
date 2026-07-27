#!/usr/bin/env bash
# 로컬 런타임 설치/갱신 — 데몬(adcost·wing·rocket)을 iCloud Drive 밖에서 돌린다.
#
# 왜: 소스/.venv가 iCloud(com~apple~CloudDocs)에 있으면 iCloud가 파일을 클라우드로
#     추방(dataless)해, launchd 데몬이 playwright import 시 'OSError: Resource deadlock
#     avoided'로 크래시 루프에 빠진다. 데몬이 실행하는 Python 런타임과 스크립트는
#     iCloud가 절대 추방 못 하는 로컬 디스크(~/.ohisell)에 둔다.
#
# 멱등: 여러 번 실행해도 안전. 페처 .py를 iCloud 소스에서 로컬로 복사 + 의존성 재설치.
# 재배포: iCloud의 tools/*.py를 수정한 뒤 이 스크립트를 다시 실행하면 로컬에 반영된다.
set -euo pipefail

REPO_TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # iCloud 소스 tools/
LOCAL_HOME="$HOME/.ohisell"
LOCAL_VENV="$LOCAL_HOME/venv"
LOCAL_TOOLS="$LOCAL_HOME/tools"
PY_VERSION_PIN="3.14"
PLAYWRIGHT_PIN="1.60.0"
REQUESTS_PIN="2.33.1"

echo "==> 로컬 런타임 경로: $LOCAL_HOME"
mkdir -p "$LOCAL_TOOLS"

# 1. 로컬 venv (없으면 생성). homebrew python3.14 = 로컬 디스크.
PY_BIN="$(command -v python${PY_VERSION_PIN} || command -v python3)"
echo "==> Python: $PY_BIN ($($PY_BIN --version))"
if [ ! -x "$LOCAL_VENV/bin/python3" ]; then
  echo "==> venv 생성: $LOCAL_VENV"
  "$PY_BIN" -m venv "$LOCAL_VENV"
fi

# 2. 의존성 (페처는 requests + playwright만 import). 버전 핀 = 캐시된 브라우저 호환.
echo "==> 의존성 설치 (requests==$REQUESTS_PIN, playwright==$PLAYWRIGHT_PIN)"
"$LOCAL_VENV/bin/pip" install --quiet --upgrade pip
"$LOCAL_VENV/bin/pip" install --quiet "requests==$REQUESTS_PIN" "playwright==$PLAYWRIGHT_PIN"

# 3. playwright 브라우저 — 보통 ~/Library/Caches/ms-playwright(로컬)에 이미 있음.
#    핀 버전과 캐시가 일치하면 즉시 종료, 불일치면 해당 브라우저만 다운로드.
echo "==> playwright chromium 확인/설치"
"$LOCAL_VENV/bin/playwright" install chromium >/dev/null 2>&1 || true

# 4+5. 페처 스크립트 복사 + plist 렌더/설치/reload.
# macOS 기본 bash는 3.2 → 연관배열(declare -A) 미지원. name:script 쌍을 공백 구분 문자열로.
echo "==> 페처 복사 + plist 설치"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS"
UID_NUM="$(id -u)"
# ★2026-07-27: Chrome 상주 supervisor 잡(wing-chrome·ohitech-chrome·rocket-chrome)은 폐기됐다.
#   창을 닫아도 launchd가 Chrome을 되살리던 원인 → 이제 poll 데몬이 fetch 때만 Chrome을 띄우고
#   닫는다. 남는 잡은 poll 데몬 4개뿐(adcost·wing·rocket·ohitech-ad).
#
# ★구 supervisor 잡 자동 제거(codex R1 P1#1): 설치 목록에서 빼기만 하면 **이미 로드된 구 잡은
#   그대로 살아남는다** — 실행 중인 파이썬 프로세스는 아래 cp가 스크립트를 덮어써도 교체되지 않아
#   구 코드가 계속 Chrome을 상주시킨다(= 이번 전환의 목적이 통째로 무효화). 문서 안내로는 세 번
#   못 막았다(원칙: 부탁이 아니라 구조로) → 여기서 bootout·plist 삭제까지 수행하고, 잔존하면
#   설치를 실패로 끝낸다. (구 plist 소멸을 재확인한 뒤 2026-07-27 no-op 스텁 chrome-supervise는
#   페처 코드에서 완전히 제거했다 — 이 bootout·plist 삭제 로직만이 유일한 방어선이다.)
_deprecated_left=0
for _dep in wing-chrome ohitech-chrome rocket-chrome; do
  _dep_plist="$LAUNCH_AGENTS/com.ohisell.$_dep.plist"
  _dep_loaded=0
  launchctl print "gui/$UID_NUM/com.ohisell.$_dep" >/dev/null 2>&1 && _dep_loaded=1
  if [ "$_dep_loaded" = "1" ]; then
    launchctl bootout "gui/$UID_NUM/com.ohisell.$_dep" 2>/dev/null || true
    for _i in $(seq 1 25); do
      launchctl print "gui/$UID_NUM/com.ohisell.$_dep" >/dev/null 2>&1 || break
      sleep 1
    done
    if launchctl print "gui/$UID_NUM/com.ohisell.$_dep" >/dev/null 2>&1; then
      echo "    ⚠️ 구 supervisor com.ohisell.$_dep bootout 실패 — 상주 Chrome이 남는다."
      _deprecated_left=1
      continue
    fi
    echo "    구 supervisor com.ohisell.$_dep → bootout 완료"
  fi
  if [ -f "$_dep_plist" ]; then
    rm -f "$_dep_plist"
    echo "    구 plist 삭제: $_dep_plist"
  fi
done
if [ "$_deprecated_left" = "1" ]; then
  echo "==> ❌ 설치 중단: 구 Chrome supervisor 잡을 제거하지 못했습니다."
  echo "    수동: launchctl bootout gui/$UID_NUM/com.ohisell.<label> 후 재실행"
  exit 1
fi
for pair in "adcost:ad_cost_browser_fetcher.py" "wing:wing_browser_fetcher.py" "rocket:rocket_supplier_fetcher.py" "ohitech-ad:ohitech_ad_fetcher.py"; do
  name="${pair%%:*}"
  script="${pair##*:}"
  cp -f "$REPO_TOOLS/$script" "$LOCAL_TOOLS/$script"
  echo "    $script 복사"
  tmpl="$REPO_TOOLS/com.ohisell.$name.plist"
  [ -f "$tmpl" ] || { echo "    (템플릿 없음: $name)"; continue; }
  dest="$LAUNCH_AGENTS/com.ohisell.$name.plist"
  sed -e "s#__PYTHON__#$LOCAL_VENV/bin/python3#g" \
      -e "s#__SCRIPT__#$LOCAL_TOOLS/$script#g" \
      -e "s#__HOME__#$HOME#g" \
      "$tmpl" > "$dest"
  # 리로드 검증용 구 잡 PID 캡처(label은 list의 3번째 컬럼).
  _old_pid="$(launchctl list 2>/dev/null | awk -v n="com.ohisell.$name" '$3==n{print $1}')"
  launchctl bootout "gui/$UID_NUM/com.ohisell.$name" 2>/dev/null || true
  # bootout 완료 대기 — 고정 sleep은 느린 종료(fetch 중인 데몬이 Chrome을 최대 15초 정리)와
  # 레이스로 bootstrap이 '미로드'로 조용히 실패한다. 잡이 실제로 사라질 때까지
  # (launchctl print 실패) 최대 25초 폴링.
  for _i in $(seq 1 25); do
    launchctl print "gui/$UID_NUM/com.ohisell.$name" >/dev/null 2>&1 || break
    sleep 1
  done
  # 25초 후에도 잔존 = bootout 실패 → 강제 1회 더(레이스/멈춤 잡 방어).
  if launchctl print "gui/$UID_NUM/com.ohisell.$name" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID_NUM/com.ohisell.$name" 2>/dev/null || true
    sleep 3
  fi
  launchctl bootstrap "gui/$UID_NUM" "$dest" 2>/dev/null || launchctl load "$dest" 2>/dev/null || true
  sleep 1
  _new_pid="$(launchctl list 2>/dev/null | awk -v n="com.ohisell.$name" '$3==n{print $1}')"
  # 성공 = 잡이 로드돼 있고 PID가 갱신됨(구 잡 잔존이면 PID 동일 → 실패). 이전에 미로드면
  # _old_pid가 비어 어떤 로드든 성공. 구 잡이 그대로 살아있는 silent stale-deploy 차단(codex R3).
  if launchctl print "gui/$UID_NUM/com.ohisell.$name" >/dev/null 2>&1 \
     && { [ -z "$_old_pid" ] || [ "$_new_pid" != "$_old_pid" ]; }; then
    echo "    com.ohisell.$name → 설치+reload (pid ${_old_pid:-none}→${_new_pid:-?})"
  else
    echo "    com.ohisell.$name → ⚠️ reload 실패(구 잡 잔존 가능, pid=${_new_pid:-?}) — 수동: launchctl bootout gui/$UID_NUM/com.ohisell.$name && launchctl bootstrap gui/$UID_NUM $dest"
  fi
done

# 6. 스케줄러 워치독 폴 데몬(S5b S5) — 별도 블록(위 loop와 독립).
#    브라우저/플레이라이트 불필요(requests만). prod /api/scheduler/health 폴 → 비정상 시 Mac 알림.
echo "==> 스케줄러 워치독 폴 설치"
WD_SCRIPT="scheduler_watchdog_poll.py"
cp -f "$REPO_TOOLS/$WD_SCRIPT" "$LOCAL_TOOLS/$WD_SCRIPT"
echo "    $WD_SCRIPT 복사"
WD_TMPL="$REPO_TOOLS/com.ohisell.scheduler-watchdog.plist"
if [ -f "$WD_TMPL" ]; then
  WD_DEST="$LAUNCH_AGENTS/com.ohisell.scheduler-watchdog.plist"
  sed -e "s#__PYTHON__#$LOCAL_VENV/bin/python3#g" \
      -e "s#__SCRIPT__#$LOCAL_TOOLS/$WD_SCRIPT#g" \
      -e "s#__HOME__#$HOME#g" \
      "$WD_TMPL" > "$WD_DEST"
  launchctl bootout "gui/$UID_NUM/com.ohisell.scheduler-watchdog" 2>/dev/null || true
  sleep 2
  launchctl bootstrap "gui/$UID_NUM" "$WD_DEST" 2>/dev/null || launchctl load "$WD_DEST" 2>/dev/null || true
  echo "    com.ohisell.scheduler-watchdog → 설치+reload"
fi

echo "==> 완료. 데몬이 로컬 런타임($LOCAL_VENV)으로 기동됨."
echo "    상태: launchctl list | grep com.ohisell"
