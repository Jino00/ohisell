#!/usr/bin/env bash
# safe_merge.sh — PR 병합 전 CI·충돌을 구조로 확인한다 (2026-08-07 신설)
#
# ★왜 스크립트인가: 이 repo는 **private + GitHub Free**라 서버측 required status checks를
#   걸 수 없다(classic protection·rulesets 둘 다 403 "Upgrade to GitHub Pro"). 즉 CI는
#   결과를 보여줄 뿐 빨간불에서 병합을 막지 못한다. 그런데 "보이면 본다"는 이 repo에서
#   반복적으로 깨졌다 — GFA 배너 63일 거짓 빨강 · 프론트 배포 clobber 3회(발견은 우연) ·
#   회색 CI 칩도 Jino가 짚어서 알았다. safe_deploy.sh·next_ids.sh와 같은 이유로,
#   규칙이 아니라 도구로 둔다.
#
# ★왜 차단이 아니라 거부+자백인가: 전역 CLAUDE.md §1은 "게이트 부활은 되돌릴 수 없게 됐다는
#   근거가 있을 때만"이라고 한다. 빨간 병합은 revert가 되므로 하드 게이트의 근거가 없다.
#   그래서 --force 탈출구를 두되 **자백을 출력하고 로그에 남긴다**(↗️스코프 밖·🔀위임 공지와
#   같은 계열 — 차단이 아니라 무의식을 의식으로 바꾸는 장치).
#
# 막는 것 셋 (전부 2026-08-07에 실제로 겪은 것):
#   ① 빨간 CI에서 병합
#   ② **검사가 아예 없는데 통과처럼 보이는 것** — 교훈 #123("발견 0건과 실행 안 됨은 같은
#      숫자로 보인다"). checks가 0건이면 초록이 아니라 «모름»이다.
#   ③ CONFLICTING 병합 시도 — PR #231이 그랬고, 수동으로 확인 안 했으면 못 봤다.
#      (그때 실제 원인은 병행 세션의 ref·교훈 번호 선점이었다)
#
# 사용:
#   scripts/safe_merge.sh            # 현재 브랜치의 PR
#   scripts/safe_merge.sh 233        # PR 번호 지정
#   scripts/safe_merge.sh 233 --force  # 빨간불/미검사인 걸 알면서 병합(자백 기록됨)
#   MERGE_WAIT=900 scripts/safe_merge.sh   # pending 대기 상한(초, 기본 600)

set -euo pipefail

FORCE=0
PR=""
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    [0-9]*)  PR="$a" ;;
    *) echo "알 수 없는 인자: $a" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null || { echo "❌ gh CLI 없음"; exit 2; }

if [ -z "$PR" ]; then
  PR=$(gh pr view --json number --jq .number 2>/dev/null || true)
  [ -n "$PR" ] || { echo "❌ 현재 브랜치에 PR이 없다. PR 번호를 인자로 줄 것."; exit 2; }
fi

LOG="${TMPDIR:-/tmp}/safe_merge.log"
say() { printf '%s\n' "$*"; }

# ── 1) 상태 — 이미 병합됐거나 닫힌 PR을 다시 병합하지 않는다
state=$(gh pr view "$PR" --json state --jq .state)
if [ "$state" != "OPEN" ]; then
  say "❌ PR #$PR 은 $state 상태다. 병합할 것이 없다."
  exit 1
fi

# ── 2) 충돌 — mergeable은 GitHub가 비동기로 계산해서 처음엔 UNKNOWN이 온다. 잠깐 기다린다.
mergeable=""
for _ in 1 2 3 4 5 6; do
  mergeable=$(gh pr view "$PR" --json mergeable --jq .mergeable)
  [ "$mergeable" = "UNKNOWN" ] || break
  sleep 5
done
if [ "$mergeable" = "CONFLICTING" ]; then
  say "❌ PR #$PR 이 CONFLICTING 이다 — main이 앞서 갔다(병행 세션)."
  say "   덮지 말고: git fetch origin && git merge origin/main → 충돌 해소 → 재시도."
  say "   ⚠️ref/교훈/D-N 번호가 겹쳤을 수 있다. scripts/next_ids.sh 로 확인할 것."
  exit 1
fi
[ "$mergeable" = "UNKNOWN" ] && say "⚠️ mergeable=UNKNOWN (GitHub 계산 중) — 충돌 판정 없이 진행한다."

# ── 3) 체크 — pending이면 기다린다. **0건이면 초록이 아니라 «모름»이다**(교훈 #123).
WAIT="${MERGE_WAIT:-600}"
deadline=$(( $(date +%s) + WAIT ))
while :; do
  raw=$(gh pr checks "$PR" 2>&1 || true)
  if printf '%s' "$raw" | grep -q "no checks reported"; then
    verdict="NONE"; break
  fi
  if printf '%s' "$raw" | grep -qw "pending"; then
    if [ "$(date +%s)" -ge "$deadline" ]; then verdict="TIMEOUT"; break; fi
    say "… CI 대기 중 ($(( deadline - $(date +%s) ))초 남음)"
    sleep 20; continue
  fi
  if printf '%s' "$raw" | grep -qwE "fail|failure"; then verdict="FAIL"; else verdict="PASS"; fi
  break
done

say ""
say "$raw"
say ""

case "$verdict" in
  PASS) say "✅ CI 전건 통과 — PR #$PR 병합한다." ;;
  NONE) say "❌ 체크가 **0건**이다. 이건 초록이 아니라 «모름»이다(교훈 #123: 발견 0건과 실행 안 됨은 같은 숫자로 보인다)."
        say "   워크플로가 이 브랜치에 안 걸렸거나 아직 시작 전이다. .github/workflows/ci.yml 확인." ;;
  FAIL) say "❌ CI 실패가 있다. 위 URL의 로그를 먼저 읽을 것." ;;
  TIMEOUT) say "❌ ${WAIT}초 안에 CI가 안 끝났다. MERGE_WAIT 를 늘리거나 나중에 재시도." ;;
esac

if [ "$verdict" != "PASS" ]; then
  if [ "$FORCE" != "1" ]; then
    say ""
    say "   그래도 병합하려면: scripts/safe_merge.sh $PR --force"
    exit 1
  fi
  # 자백 — 화면과 로그 양쪽에. 되돌릴 수 있으므로 막지는 않되, 조용히 지나가지도 않는다.
  line="$(date '+%Y-%m-%d %H:%M:%S %Z') PR#$PR --force 병합 (verdict=$verdict) by $(git config user.name 2>/dev/null || echo '?')"
  say ""
  say "⚠️⚠️ 강제 병합: $line"
  printf '%s\n' "$line" >> "$LOG"
  say "   기록: $LOG"
fi

gh pr merge "$PR" --merge
say "✅ PR #$PR 병합 완료."
