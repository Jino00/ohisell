#!/usr/bin/env bash
# setup_obsidian.sh — 이 repo를 옵시디언 볼트로 «열기 전에» 설정을 심는다 (D-NAO-165)
#
# ★왜 «열기 전»인가 (2026-08-10 실측):
#   이 repo는 파일 163,106개(frontend/node_modules 포함)이고, `.claude/worktrees` 아래에만
#   markdown이 7,000개다 — 대부분 LESSONS_LEARNED.md·HANDOFF의 **워크트리 사본**이다.
#   설정 없이 열면 ①인덱싱이 오래 걸리고 ②그래프가 같은 문서의 사본 수천 개로 뒤덮여
#   지혜층이 안 보인다. 「열고 나서 제외 설정」은 순서가 틀렸다(이미 인덱싱된 뒤다).
#
# `.obsidian/`은 커밋하지 않으므로(개인 UI 설정) 이 스크립트가 재현 수단이다.
# 이미 있는 설정은 덮지 않는다(--force로 강제).
#
# 사용: scripts/setup_obsidian.sh [--force]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG_DIR="$REPO/.obsidian"
APP_JSON="$CFG_DIR/app.json"

if [ -f "$APP_JSON" ] && [ "${1:-}" != "--force" ]; then
  echo "이미 설정됨: $APP_JSON"
  echo "  덮어쓰려면: $0 --force"
  exit 0
fi

mkdir -p "$CFG_DIR"

# userIgnoreFilters = 옵시디언 「Excluded files」. 인덱싱·검색·그래프에서 빠진다.
# 여기 없는 것을 나중에 추가할 땐 앱 Settings → Files & Links에서 해도 되고 이 파일을 고쳐도 된다.
cat > "$APP_JSON" <<'JSON'
{
  "userIgnoreFilters": [
    "frontend/node_modules/",
    "backend/",
    "frontend/",
    "node_modules/",
    ".claude/worktrees/",
    ".venv/",
    "docs/archive/",
    ".claude/anchors/"
  ],
  "alwaysUpdateLinks": true,
  "showUnsupportedFiles": false
}
JSON

# 그래프에서 지혜층이 먼저 보이도록 시작 노트를 지정한다.
cat > "$CFG_DIR/core-plugins.json" <<'JSON'
{
  "file-explorer": true,
  "global-search": true,
  "graph": true,
  "backlink": true,
  "outgoing-link": true,
  "tag-pane": true,
  "page-preview": true,
  "starred": true,
  "outline": true,
  "word-count": false
}
JSON

echo "✅ 옵시디언 설정 생성: $APP_JSON"
echo
echo "제외 경로(인덱싱에서 빠짐):"
python3 -c "import json;[print('  -',x) for x in json.load(open('$APP_JSON'))['userIgnoreFilters']]"
echo
echo "인덱싱 대상 markdown 수:"
find "$REPO" -name '*.md' \
  -not -path '*/node_modules/*' -not -path '*/.claude/worktrees/*' \
  -not -path '*/backend/*' -not -path '*/frontend/*' \
  -not -path '*/docs/archive/*' -not -path '*/.venv/*' 2>/dev/null | wc -l | xargs echo "  "
echo
echo "이제 열면 된다: Obsidian → Open folder as vault →"
echo "  $REPO"
echo "시작 노트: docs/wiki/WISDOM.md"
