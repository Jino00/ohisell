#!/usr/bin/env bash
# setup_fetcher_autologin.sh — 페처 자동 로그인 2층(Keychain) 활성화.
#
# 배경(2026-08-03): coupang_auth의 3층 복구 중 ①SSO 재발급(비번 없음)·③알림은 코드만으로
# 동작하지만, ②Keychain 자동 로그인은 **계정별 등록이 없으면 도달 불가**다(적대적 리뷰 P1).
# 실제로 배포 직후 라이브 설정에 로그인ID 키가 없어 ②가 전부 dead code였다.
# 이 스크립트가 그 빈칸을 채운다 — 계정마다 1회만 실행하면 된다.
#
# ★비밀번호는 이 스크립트가 저장하지도 출력하지도 않는다. `security`가 프롬프트로 직접
#   받아 Keychain에 넣는다(화면에 안 보임). 평문·로그·git 어디에도 남지 않는다.
# ★멱등: 여러 번 실행해도 안전(-U가 기존 항목을 갱신).
set -euo pipefail

SERVICE="ohisell-coupang-ad"

# (표시명, config 경로, 로그인ID를 담을 키)
TARGETS=(
  "오하이테크 광고센터|$HOME/.ohisell_ohitech_ad.json|ohitech_ad_login_id"
  "오하이테크 공급자허브|$HOME/.ohisell_rocket_fetcher.json|supplier_login_id"
)

echo "════════════════════════════════════════════════════════════"
echo " 페처 자동 로그인(2층) 셋업"
echo " 비밀번호는 화면에 보이지 않고, 이 스크립트는 저장·출력하지 않습니다."
echo "════════════════════════════════════════════════════════════"
echo

for entry in "${TARGETS[@]}"; do
  IFS='|' read -r NAME CFG KEY <<<"$entry"
  echo "── $NAME"

  if [ ! -f "$CFG" ]; then
    echo "   ⏭  설정 파일 없음($CFG) — 건너뜁니다."
    echo
    continue
  fi

  CUR="$(/usr/bin/python3 -c "
import json,sys
try:
    print(json.load(open('$CFG', encoding='utf-8')).get('$KEY') or '')
except Exception:
    print('')
" 2>/dev/null || echo "")"

  if [ -n "$CUR" ]; then
    echo "   현재 로그인 ID: $CUR"
    printf "   그대로 쓰시겠습니까? [Y/n] "
    read -r keep < /dev/tty
    if [ "${keep:-Y}" = "n" ] || [ "${keep:-Y}" = "N" ]; then CUR=""; fi
  fi

  if [ -z "$CUR" ]; then
    printf "   %s 로그인 ID를 입력하세요(건너뛰려면 그냥 Enter): " "$NAME"
    read -r CUR < /dev/tty
    if [ -z "$CUR" ]; then
      echo "   ⏭  건너뜀 — 이 페처는 ①SSO 재발급까지만 동작합니다."
      echo
      continue
    fi
    # config에 기록(다른 키는 건드리지 않는다)
    /usr/bin/python3 - "$CFG" "$KEY" "$CUR" <<'PY'
import json, sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as f:
    cfg = json.load(f)
cfg[key] = val
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print(f"   ✓ {path}에 {key} 기록")
PY
  fi

  echo "   비밀번호를 Keychain에 저장합니다(두 번 입력, 화면에 안 보임)."
  security add-generic-password -U -s "$SERVICE" -a "$CUR" -w
  # 저장 확인 — 값을 출력하지 않고 존재 여부만 본다.
  if security find-generic-password -s "$SERVICE" -a "$CUR" >/dev/null 2>&1; then
    echo "   ✓ Keychain 저장 확인($CUR)"
  else
    echo "   ❌ Keychain 저장 확인 실패 — 다시 시도하세요."
    exit 1
  fi
  echo
done

echo "════════════════════════════════════════════════════════════"
echo " 완료. 다음 수집부터 2층(자동 로그인)이 동작합니다."
echo " 확인: 세션이 풀린 뒤 로그에 '자동 로그인 성공'이 뜨는지 보세요."
echo "   tail -f ~/.ohisell_ohitech_ad.launchd.log"
echo "   tail -f ~/.ohisell_rocket_fetcher.launchd.log"
echo "════════════════════════════════════════════════════════════"
