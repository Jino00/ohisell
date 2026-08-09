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

# ★교훈 제목은 형식이 **세 가지**다 — #139까지 `## 139. 제목`, #140부터 `## #140 — 제목`,
#   그리고 **`## 교훈 #186 — 제목`**(2026-08-09에 발견. 이 파일 안 «못 잡는다» 목록에
#   `## 교훈 153`이 예시로 적혀 있었는데 그게 실제로 왔다).
#   ★★그때 **드리프트 경보가 울리지 않았다**: 느슨한 후보 패턴도 숫자 앞의 «교훈 »을 몰라
#   strict와 **나란히** 못 잡았기 때문이다. 이 파일이 #1000 자릿수에 대해 미리 걱정해 둔
#   «두 카운트가 같이 줄어 검사가 침묵한다»가 자릿수가 아니라 **접두어**로 실현된 것이다.
#   → 두 패턴에 접두어를 **같이** 넣는다. 한쪽만 넣으면 경보만 울리고 답은 계속 틀린다.
#   구 정규식(`^## [0-9]+\.`)은 앞의 것만 봐서 실제 최댓값 152인데 139를 뱉었고, 그 상태로
#   세 세션을 이월했다(HANDOFF 08-05·08-06). 번호 충돌을 막으려고 만든 도구가 조용히 옛 번호를
#   준 것 — 이 파일이 대체하려던 실패와 같은 종류다. 그래서 두 형식을 다 읽고, 아래에서
#   형식 드리프트도 감지한다.
_LESSON_PREFIX='(교훈 +)?'                    # 세 번째 형식 `## 교훈 #186 —`
_LESSON_RE="^#{2,3} +${_LESSON_PREFIX}#?[0-9]{1,3}\.?( |$)"   # 연도(`## 2026-08-06`)는 자릿수·구분자로 걸러진다
_lesson_nums() { grep -oE "$_LESSON_RE" 2>/dev/null | grep -oE '[0-9]+'; }
_max_lesson() { _lesson_nums | sort -n | tail -1; }

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

# ★형식 드리프트 감지 — 이 도구의 실제 실패 모드는 "틀린 답"이 아니라 **조용히 옛 답**이었다.
#   느슨한 패턴(숫자로 시작하는 교훈 제목 후보)이 위 정규식보다 많이 걸리면 형식이 또 바뀐 것이다.
#   그때 여기서 말하지 않으면, 다음 사람은 스크립트를 믿고 이미 쓰인 번호를 또 쓴다.
#   ⚠️커버 범위는 부분적이다(실측): `#### #153`·`## 153)`는 잡고, `## [153]`·`## 교훈 153`은
#   못 잡는다 — 후자까지 잡으려면 `^#+.*[0-9]`가 되어 날짜 든 제목마다 거짓 경고가 난다.
#   #140→#152 형식 변경(=실제로 3세션을 이월시킨 그 결함)은 이 검사에 걸린다.
# 후보 패턴은 **제목 모양**까지 요구한다 — 숫자 뒤에 구분자나 줄끝이 와야 한다.
# 그냥 `^#+ *#?[0-9]`로 두면 본문의 `#151에서 고친 것은…`처럼 참조로 시작하는 줄이 걸려
# 거짓 경고가 난다(도입 직후 실제로 걸렸다). 시끄러운 가드는 읽히지 않으므로 그게 더 나쁘다.
_LESSON_CAND_RE="^#+ *${_LESSON_PREFIX}#?[0-9]{1,3}([ .:)—-]|\$)"
strict_n=$(grep -cE "$_LESSON_RE" "$LESSONS" 2>/dev/null || true)
loose_n=$(grep -cE "$_LESSON_CAND_RE" "$LESSONS" 2>/dev/null || true)
: "${strict_n:=0}"; : "${loose_n:=0}"
if [[ "$loose_n" -gt "$strict_n" ]]; then
  echo
  echo "⚠️  교훈 제목 형식이 바뀐 것 같다 — 인식 ${strict_n}건 / 제목 후보 ${loose_n}건."
  echo "    위 교훈 번호를 믿지 말고 확인할 것:"
  echo "    grep -nE '$_LESSON_CAND_RE' $LESSONS | grep -vE '$_LESSON_RE'"
fi

# ★자릿수 상한 경고 — 정규식이 3자리까지만 본다. #1000이 되면 strict·후보가 **같이** 못 잡아
#   위 드리프트 검사가 침묵하고(두 카운트가 나란히 줄어든다) 이 도구는 다시 조용히 옛 번호를
#   준다 = 이 파일이 존재하는 이유와 똑같은 실패. 미리 시끄럽게 만들어 둔다.
if [[ "$next_lesson" -ge 900 || "$next_dnao" -ge 900 ]]; then
  echo
  echo "⚠️  번호가 3자리 상한에 접근했다 — 정규식 자릿수({1,3})를 넓혀야 한다."
  echo "    넓힐 때 연도(\`## 2026-08-06\`)가 번호로 잡히지 않는지 같이 확인할 것."
fi

behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [[ "$behind" -gt 0 ]]; then
  echo
  echo "⚠️  origin/main이 ${behind}커밋 앞서 있다 — 병행 세션이 그 사이에 번호를 더 썼을 수 있다."
  echo "    번호를 부여하기 전에 병합할 것:  git merge origin/main"
fi
