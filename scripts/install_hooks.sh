#!/usr/bin/env bash
# install_hooks.sh — `.githooks/`를 이 클론의 훅 경로로 등록한다 (issue #247, 2026-08-07)
#
# 왜 스크립트가 필요한가: `core.hooksPath`는 **커밋되지 않는 로컬 설정**이라, 훅 파일을
#   repo에 넣는 것만으로는 아무도 실행하지 않는다. 새 클론마다 한 번 돌려야 한다.
#
# 워크트리에서 돌려도 된다 — config는 클론 단위로 공유되므로 한 번이면 전부에 걸린다.
# (그리고 훅 자신이 워크트리를 판별해 통과시키므로 워크트리 작업엔 영향이 없다.)

set -euo pipefail

root=$(git rev-parse --show-toplevel)
common=$(cd "$(git rev-parse --git-common-dir)" && pwd -P)
# 워크트리에서 실행한 경우 실제 메인 체크아웃 경로를 잡는다(.git 의 부모).
main_root=$(dirname "$common")

# ★상대 경로(`core.hooksPath=.githooks`)를 쓰면 안 된다 — 2026-08-07 실제 설치하다 발견.
#   상대값은 **각 워킹트리 기준**으로 풀린다. 그래서 공유 메인 폴더가 `.githooks`가 없는 옛
#   브랜치에 체크아웃돼 있으면 **훅이 조용히 사라진다** — 하필 그 상태가 이 가드가 막으려는
#   바로 그 상황이다(설치 시점에 루트가 실제로 `claude/rocket-1p-new-unlinked`에 있었다).
#   → 워킹트리 밖(`.git/ohisell-hooks/`)에 **복사**하고 절대경로로 건다. 브랜치와 무관해진다.
src="$root/.githooks/pre-commit"
if [ ! -f "$src" ]; then
  echo "❌ $src 가 없다. 이 브랜치를 main 기준으로 갱신한 뒤 다시 실행할 것." >&2
  exit 1
fi

dest_dir="$common/ohisell-hooks"
mkdir -p "$dest_dir"
cp "$src" "$dest_dir/pre-commit"
chmod +x "$dest_dir/pre-commit"
git config core.hooksPath "$dest_dir"

echo "✅ core.hooksPath = $dest_dir  (클론: $main_root)"
# ⚠️백틱을 쓰지 말 것 — 큰따옴표 안에서 명령치환돼 파일명이 통째로 사라진다(첫 실행에서 실제로 겪었다).
echo '   ⚠️ 훅은 복사본이다 — .githooks/pre-commit 을 고치면 이 스크립트를 다시 돌릴 것.'
echo "   확인: git config --get core.hooksPath"
echo "   해제: git config --unset core.hooksPath"
echo
echo "이 훅이 하는 일 — **공유 메인 체크아웃에서만** 동작한다(워크트리는 즉시 통과):"
echo "  · main이 아닌 브랜치에서 커밋 → 거부(--no-verify 로 우회 가능)"
echo "  · main 위 커밋에 새 파일이 있으면 목록을 띄움(거부는 안 함)"
echo "  근거: 병행 세션이 공유 폴더에 써 둔 미추적 파일이 남의 커밋에 쓸려 들어간 사고 2회(issue #247)"

# 실행 경로가 root와 다르면(=워크트리) 알려준다 — 사용자가 어디에 걸렸는지 헷갈리지 않게.
[ "$root" != "$main_root" ] && echo "(워크트리 $root 에서 실행됨 — 설정은 클론 전체에 적용된다)"
exit 0
