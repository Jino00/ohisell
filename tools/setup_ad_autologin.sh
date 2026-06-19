#!/usr/bin/env bash
# 광고비 페처 빈틈 0 셋업 — 사용자는 비밀번호 2개만 입력하면 된다.
#   B) 쿠팡 광고 비밀번호 → macOS Keychain (자동 로그인 활성화). 평문·로그·git 0.
#   A2) Mac 03:00 깨우기 → 야간 keycloak 갱신이 돌도록 (sudo=Mac 로그인 비번).
# 비밀번호는 화면에 안 보이고, 이 스크립트는 비번을 저장/출력하지 않는다.
set -euo pipefail

CFG="$HOME/.ohisell_ad_fetcher.json"
SERVICE="ohisell-coupang-ad"

# 로그인 아이디는 config에서 읽음(없으면 ofixohi)
LOGIN_ID="$(/usr/bin/python3 -c "import json,os;print(json.load(open(os.path.expanduser('$CFG'))).get('ad_login_id','ofixohi'))" 2>/dev/null || echo ofixohi)"

echo "════════════════════════════════════════════════════════"
echo " 광고비 자동 로그인 + 야간 갱신 셋업"
echo " 로그인 아이디: $LOGIN_ID  (다르면 Ctrl+C 후 알려주세요)"
echo "════════════════════════════════════════════════════════"
echo
echo "[1/2] 쿠팡 광고 비밀번호를 Keychain에 저장합니다."
echo "      → 잠시 후 비밀번호를 두 번 입력하세요(화면에 안 보임)."
echo
security add-generic-password -U -s "$SERVICE" -a "$LOGIN_ID" -w
echo "  ✓ Keychain 저장 완료"
echo
echo "[2/2] Mac을 매일 02:58에 깨웁니다(야간 세션 갱신용)."
echo "      → Mac 로그인 비밀번호를 입력하세요."
echo
sudo pmset repeat wakeorpoweron MTWRFSU 02:58:00
echo "  ✓ 예약 깨우기 설정 완료"
echo
echo "════════════════════════════════════════════════════════"
echo " 검증"
echo "════════════════════════════════════════════════════════"
if security find-generic-password -s "$SERVICE" -a "$LOGIN_ID" -w >/dev/null 2>&1; then
  echo "  ✓ Keychain 자격증명 등록됨"
else
  echo "  ✗ Keychain 등록 실패 — 다시 실행하세요"
fi
echo "  예약 깨우기:"
pmset -g sched | grep -i "repeat" -A1 | sed 's/^/    /'
echo
echo "완료. 이제 keycloak이 만료돼도 자동 로그인으로 복구되고,"
echo "밤사이엔 02:58 갱신으로 세션이 끊기지 않습니다."
