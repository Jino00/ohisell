#!/usr/bin/env bash
# next_ids.sh — 다음에 쓸 D-NAO 번호·교훈 번호를 **origin/main과 내 브랜치 양쪽에서** 계산한다.
#
# ★존재 이유(2026-08-04, 하루에 두 번·누적 세 번 충돌): 병행 세션이 활발해서, 내 브랜치의
#   최댓값만 보고 번호를 부여하면 **같은 번호가 다른 결정에 붙는다**. 실제로 D-NAO-146·147·148과
#   교훈 #129·#130이 각각 두 내용에 붙었고, 그때마다 사후에 재번호+참조 갱신을 해야 했다
#   (내 것을 뒤로 미는 게 관례 — main이 트렁크다).
# ★왜 문서 규칙이 아니라 스크립트인가: "번호 부여 전에 fetch 해라"는 이미 HANDOFF에 세 번 적혔고
#   세 번 다 안 지켜졌다. 사람이 기억할 필요가 없어야 지켜진다(safe_deploy.sh와 같은 이유).
#
# 사용: scripts/next_ids.sh            # fetch 후 계산
#       scripts/next_ids.sh --no-fetch # 오프라인(네트워크 없을 때)
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

TRACK="docs/tracks/active/track_naver-ad-optimization.md"
LESSONS=".claude/memory/LESSONS_LEARNED.md"

[[ "${1:-}" == "--no-fetch" ]] || git fetch -q origin

# 번호 추출기 — 정의 줄만 센다(본문 인용까지 세면 항상 같은 값이라 무의미하진 않지만,
# 최댓값만 쓰므로 인용을 포함해도 결과는 같다. 단순함을 택한다).
_max_dnao() { grep -oE 'D-NAO-[0-9]+' 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1; }
_max_lesson() { grep -oE '^## [0-9]+\.' 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1; }

_from_ref() {  # $1=ref($2=파일), 없으면 0
  git show "$1:$2" 2>/dev/null || true
}

main_dnao=$(_from_ref origin/main "$TRACK" | _max_dnao || true)
mine_dnao=$(cat "$TRACK" 2>/dev/null | _max_dnao || true)
main_lesson=$(_from_ref origin/main "$LESSONS" | _max_lesson || true)
mine_lesson=$(cat "$LESSONS" 2>/dev/null | _max_lesson || true)

: "${main_dnao:=0}"; : "${mine_dnao:=0}"; : "${main_lesson:=0}"; : "${mine_lesson:=0}"

max() { [[ "$1" -ge "$2" ]] && echo "$1" || echo "$2"; }
next_dnao=$(( $(max "$main_dnao" "$mine_dnao") + 1 ))
next_lesson=$(( $(max "$main_lesson" "$mine_lesson") + 1 ))

echo "다음 번호 (origin/main·내 브랜치 중 큰 쪽 +1)"
echo "  D-NAO-${next_dnao}   (origin/main=${main_dnao} · 내 브랜치=${mine_dnao})"
echo "  교훈 #${next_lesson}   (origin/main=${main_lesson} · 내 브랜치=${mine_lesson})"

behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [[ "$behind" -gt 0 ]]; then
  echo
  echo "⚠️  origin/main이 ${behind}커밋 앞서 있다 — 병행 세션이 그 사이에 번호를 더 썼을 수 있다."
  echo "    번호를 부여하기 전에 병합할 것:  git merge origin/main"
fi
