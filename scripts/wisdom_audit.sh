#!/usr/bin/env bash
# wisdom_audit.sh — 주간 지혜 감사의 «기계가 할 수 있는 부분» (D-NAO-165)
#
# 왜 스크립트인가: 감사 프로토콜이 문서로만 있으면 그건 이 시스템이 경계하는
# 바로 그 실패다(교훈 #202 — 기록된 지식을 아무도 안 읽음). 지표 수집과 부채 노출은
# 기계가 하고, «패턴이냐 아니냐» 판단과 승격 제안은 세션이 한다.
#
# 출력: docs/references/wisdom_audit_<날짜>.md (append 아님 — 주차별 새 파일)
# 판단이 필요한 §1·§3·§5는 «세션이 채울 빈칸»으로 남긴다.
#
# 사용: scripts/wisdom_audit.sh            (오늘 날짜로 생성)
#       scripts/wisdom_audit.sh --stdout   (파일 안 쓰고 화면에만)
set -uo pipefail
# ★`-e`를 일부러 빼 둔다(2026-08-10, 교훈 #209 — 같은 결함을 **세 번** 고친 뒤 내린 결론).
#   이 스크립트는 «리포트»다. 여기서 grep·루프의 «못 찾음»은 거의 전부 **정상 결과**다 —
#   결번 0건 / 미처분 교훈 0건 / 부채 0건 / 승격 대상 0건 전부 «좋은 상태»인데,
#   `-e` + `pipefail` 아래서는 그때만 스크립트가 죽어 **빈 출력 + exit 1**이 된다.
#   그 화면은 「이상 없음」과 「실행 안 됨」이 구별되지 않는 가장 나쁜 실패 모양이다.
#   `|| true`를 지점마다 붙이는 방식은 **세 번 다 빠뜨렸다** — 새 섹션을 추가할 때마다
#   다시 빠뜨릴 것이므로, 구조로 바꾼다. 대신 실제 치명 오류는 아래 명시적 검사로 잡는다.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI="$REPO/docs/wiki"
LESSONS="$REPO/.claude/memory/LESSONS_LEARNED.md"
TODAY="$(TZ=Asia/Seoul date +%Y%m%d)"
TODAY_H="$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M KST')"
OUT="$REPO/docs/references/wisdom_audit_${TODAY}.md"

[ -d "$WIKI" ] || { echo "❌ 위키 폴더 없음: $WIKI" >&2; exit 1; }
[ -r "$LESSONS" ] || { echo "❌ 교훈 원장을 읽을 수 없다: $LESSONS" >&2; exit 1; }
# ★리포트가 «완주했다»를 스스로 증명하게 한다 — 중간에 죽으면 이 줄이 안 찍힌다.
trap 'echo; echo "— 감사 리포트 끝 (정상 완주) —"' EXIT

emit() {
  echo "# 주간 지혜 감사 — $TODAY_H"
  echo
  echo "> 생성: \`scripts/wisdom_audit.sh\` (기계 수집분). 절차 = \`docs/wiki/AUDIT_PROTOCOL.md\`"
  echo "> **§1·§3·§5는 판단이 필요해 빈칸이다 — 세션이 채우지 않으면 이 감사는 미완주다.**"
  echo

  # ── 지표 ────────────────────────────────────────────────────────────
  local n_pat n_debt n_ledger lo hi n_cited gaps
  n_pat=$(find "$WIKI" -maxdepth 1 -name '*.md' \
            ! -name 'README.md' ! -name 'WISDOM.md' \
            ! -name 'AUDIT_PROTOCOL.md' ! -name 'OBSIDIAN_SETUP.md' | wc -l | tr -d ' ')
  # ★'^' 앵커 필수 — 없으면 문자열을 언급만 하는 문서까지 잡혀 부채가 부풀어 보인다
  #   (2026-08-10 셋업 검증에서 실제로 2건이 4건으로 보였다).
  n_debt=$(grep -l '^enforcement: none' "$WIKI"/*.md 2>/dev/null | wc -l | tr -d ' ')

  # ★분모를 정직하게: 이 파일에서 «번호가 붙은» 교훈만 센다.
  #   #1~#174는 번호 없는 옛 형식(`### 교훈`)이라 기계가 못 센다 — 「교훈 총수」라고
  #   부르면 처분율이 실제보다 좋아 보인다(2026-08-10 첫 실행에서 77.8%로 나왔다).
  #   그래서 **범위를 명시**하고, 범위 안의 **결번까지 드러낸다**(원장 구멍 자체가 발견이다).
  local nums; nums=$(grep -oE '^## 교훈 #[0-9]+' "$LESSONS" 2>/dev/null | grep -oE '[0-9]+' | sort -un)
  n_ledger=$(echo "$nums" | grep -c . || echo 0)
  lo=$(echo "$nums" | head -1); hi=$(echo "$nums" | tail -1)
  # ★`|| true` 필수(gaps와 같은 이유): 마지막 교훈이 위키에 없으면 루프의 종료코드가 1이
  #   되고 `pipefail`+`set -e`가 스크립트를 죽인다. 「처분 안 된 교훈이 있다」는 **정상 상태**인데
  #   그때만 감사가 죽으면, 감사가 가장 필요한 순간에 아무 말도 안 하게 된다.
  n_cited=$(for n in $nums; do grep -qh "교훈 #$n\b" "$WIKI"/*.md 2>/dev/null && echo "$n" || true; done | wc -l | tr -d ' ')
  # ★`|| true` 필수 — 결번이 **하나도 없으면** grep이 exit 1을 내고 `set -e`가 스크립트를
  #   통째로 죽인다. 2026-08-10에 실제로 그랬다: 결번 #176~#185가 메워지자 감사가
  #   «조용히 아무것도 출력하지 않고» 종료했다. 「이상 없음」이 「실행 안 됨」과 같은
  #   화면으로 보이는 형태다([[green-does-not-mean-verified]]).
  gaps=$(awk -v lo="$lo" -v hi="$hi" 'BEGIN{for(i=lo;i<=hi;i++)print i}' \
         | grep -vxF -f <(echo "$nums") | tr '\n' ' ' || true)

  echo "## 지표 (기계 수집)"
  echo
  echo "| 지표 | 값 |"
  echo "|---|---|"
  echo "| 패턴 수 | $n_pat |"
  echo "| **지식 부채**(\`enforcement: none\`) | **$n_debt** |"
  echo "| 번호 교훈 (이 파일, #$lo~#$hi) | $n_ledger |"
  echo "| 그중 위키가 처분한 것 | $n_cited |"
  echo "| **처분율** (⚠️ #$lo~#$hi 구간 한정) | $(awk -v a="$n_cited" -v b="$n_ledger" 'BEGIN{if(b>0)printf "%.1f%%", a/b*100; else print "-"}') |"
  echo "| 발견 주체 (사람 질문 : 시스템) | _세션이 채울 것_ |"
  echo
  echo "> ⚠️ **처분율의 분모는 «번호가 붙은 교훈»뿐이다.** #1~#$((lo-1))는 번호 없는 옛 형식이라"
  echo "> 기계가 못 센다 — 이 값을 «전체 처분율»로 읽지 말 것([[unknown-must-not-read-as-zero]]의 사촌:"
  echo "> 못 센 것을 분모에서 빼면 비율이 좋아 보인다)."
  if [ -n "${gaps// /}" ]; then
    echo ">"
    echo "> ⚠️ **원장 결번**: \`$gaps\` — 이 번호들이 파일에 없다. 다른 곳에 적혔거나 건너뛴 것이다."
  fi
  echo

  # ── §2 부채 ─────────────────────────────────────────────────────────
  echo "## §2 지식 부채 — \`enforcement: none\`"
  echo
  if [ "$n_debt" -eq 0 ]; then
    echo "  (없음 — 드문 상태다. 정말 없는지 §3에서 새 부채를 찾을 것)"
  else
    grep -l '^enforcement: none' "$WIKI"/*.md | while read -r f; do
      printf -- '- **%s** — %s\n' "$(basename "$f" .md)" \
        "$(grep -m1 '^enforcement_target:' "$f" | sed 's/enforcement_target: *//')"
    done
  fi
  echo
  echo "> 각 부채: **이번 주에 이것 때문에 손해가 났는가?** 났으면 도구화 백로그 상위로."
  echo

  # ── 인덱스↔파일 표류 ────────────────────────────────────────────────────
  # ★왜 기계가 세는가: 2026-08-10에 실제로 갈라졌다 — `write-to-the-binding-layer`를
  #   B-4로 처분하고 **패턴 파일만** 고쳤더니 WISDOM.md는 계속 부채 2건이라고 말했다.
  #   사람이 두 곳을 맞추는 방식은 이 repo에서 반복적으로 실패했다([[text-rules-fail-build-tools]]).
  local idx_debt
  idx_debt=$(grep -oE '지식 부채, `enforcement: none`\) ⚠️ \*\*[0-9]+건' "$WIKI/WISDOM.md" 2>/dev/null \
             | grep -oE '[0-9]+' | head -1)
  echo "## §2-b 인덱스↔파일 표류"
  echo
  if [ -z "$idx_debt" ]; then
    echo "  ⚠️ WISDOM.md에서 부채 건수 문구를 못 찾았다(형식이 바뀌었나?) — 수동 확인 필요"
  elif [ "$idx_debt" = "$n_debt" ]; then
    echo "  ✅ 일치 — WISDOM.md $idx_debt건 = 파일 실측 $n_debt건"
  else
    echo "  ⚠️ **불일치** — WISDOM.md는 ${idx_debt}건이라 하는데 파일 실측은 ${n_debt}건이다."
    # ★백틱은 반드시 이스케이프 — 큰따옴표 안의 백틱은 명령 치환이라
    #   `enforcement:: command not found`가 감사 출력에 섞인다(2026-08-10 실제로 겪음).
    echo '     **파일이 정본이다**(`enforcement:` 필드). WISDOM.md를 고칠 것.'
  fi
  echo

  # ── 승격 대상 ────────────────────────────────────────────────────────
  echo "## 승격 대상 — \`principle\`인데 재발"
  echo
  grep -l '^enforcement: principle' "$WIKI"/*.md 2>/dev/null | while read -r f; do
    printf -- '- **%s** (태그: %s) — principle은 최후수단이다. 재발했으면 도구/판별자로 승격.\n' \
      "$(basename "$f" .md)" \
      "$(grep -m1 '^recurrence_tags:' "$f" | sed 's/recurrence_tags: *//')"
  done
  echo
  echo "> ★부채(\`none\`)와 승격 대상(\`principle\` 재발)은 **다른 상태다. 섞어 세지 않는다.**"
  echo

  # ── §4 재발 태그 ─────────────────────────────────────────────────────
  echo "## §4 재발 태그 (2회 이상 = 도구화 후보)"
  echo
  grep -h '^recurrence_tags:' "$WIKI"/*.md 2>/dev/null \
    | sed 's/recurrence_tags: *//' | tr -d '[]' | tr ',' '\n' \
    | sed 's/^ *//;s/ *$//' | grep -v '^$' | sort | uniq -c | sort -rn \
    | awk '{ mark = ($1 >= 2) ? " ⚠️" : ""; printf "- `%s` × %s%s\n", $2, $1, mark }'
  echo

  # ── 미처분 교훈 ──────────────────────────────────────────────────────
  echo "## §1 미처분 교훈 (위키가 인용하지 않은 것 — 최근 15개)"
  echo
  local found=0
  grep -o '^## 교훈 #[0-9]\+' "$LESSONS" | grep -o '[0-9]\+' | sort -rn | head -15 | while read -r n; do
    if ! grep -q "교훈 #$n\b" "$WIKI"/*.md 2>/dev/null; then
      printf -- '- [ ] **#%s** — %s\n' "$n" \
        "$(grep -m1 "^## 교훈 #$n " "$LESSONS" | sed "s/^## 교훈 #$n — *//")"
      found=1
    fi
  done
  [ "$found" = 0 ] && true
  echo
  echo "> 각각: **기존 패턴에 병합** / **새 패턴 신설** / **패턴 아님**(이유 한 줄) 중 하나로 처분."
  echo

  # ── 판단 빈칸 ────────────────────────────────────────────────────────
  cat <<'BLANKS'
## §3 ★기록↔배선 대조 (세션이 채울 것 — 이 감사의 핵심)

`claimed-vs-wired-is-the-default-state.md`의 표를 들추고 답한다:

- [ ] 이번 주 «이 기능은 있다»를 근거로 내린 결론이 있었나? 3단계(존재/호출/영향) 중 어디까지?
- [ ] 기록된 지식 중 **관련 코드가 읽지 않는 것**이 새로 보이는가?
- [ ] 새 쓰기 경로가 **실효 레이어**에 닿는지 확인했나? (`write-to-the-binding-layer`)
- [ ] 새로 넣은 가드를 **사고 입력으로 돌려** 봤나? (`prove-the-guard-catches-this-input`)

발견 → 그 표에 행으로 추가. **추가 자체가 이 주의 산출물이다.**

## §5 Jino에게 올릴 승격 제안

- [ ] (없으면 «없음»이라고 적을 것 — 빈칸으로 두지 않는다)
BLANKS
}

if [ "${1:-}" = "--stdout" ]; then
  emit
else
  mkdir -p "$REPO/docs/references"
  emit > "$OUT"
  echo "생성: $OUT"
  echo "→ §1·§3·§5를 세션이 채워야 감사가 완주다(빈칸이면 INCONCLUSIVE)."
fi
