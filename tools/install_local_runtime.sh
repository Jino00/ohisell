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
for pair in "adcost:ad_cost_browser_fetcher.py" "wing:wing_browser_fetcher.py" "rocket:rocket_supplier_fetcher.py"; do
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
  launchctl bootout "gui/$UID_NUM/com.ohisell.$name" 2>/dev/null || true
  sleep 2  # bootout 완료 대기 — 너무 빠른 bootstrap은 레이스로 '미로드' 실패함
  launchctl bootstrap "gui/$UID_NUM" "$dest" 2>/dev/null || launchctl load "$dest" 2>/dev/null || true
  echo "    com.ohisell.$name → 설치+reload"
done

echo "==> 완료. 데몬 3종이 로컬 런타임($LOCAL_VENV)으로 기동됨."
echo "    상태: launchctl list | grep com.ohisell"
