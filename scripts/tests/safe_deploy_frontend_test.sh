#!/usr/bin/env bash
# safe_deploy.sh --frontend 스탬프 CAS 회귀 테스트 — ssh/rsync를 shim으로 갈아끼워
# prod를 안 건드리고 "덮어쓰기 거부" 로직을 검증한다.
#
# ★왜 실행 가능한 테스트로 두는가(2026-08-06 사고): dist는 통짜 rsync라 파일 CAS가 안 걸렸고,
#   그 틈으로 병행 세션이 09:09·09:23 두 번 서로의 프론트 수정을 조용히 지웠다. "조심하기"로
#   막는 방식은 백엔드에서 이미 세 번 실패했다(safe_deploy.sh 헤더 2026-07-17 참조).
#   가드가 조용히 무력화되면 여기서 빨간불이 난다.
#
# ★이 하니스가 고정하는 계약:
#   · prod dist에 `.deploy-stamp` 가 없으면 = 가드 도입 이전 배포 → **통과**시키고 심는다.
#   · 스탬프의 커밋이 내 HEAD의 **조상**이면 통과(내 트리가 그 작업을 이미 포함).
#   · 조상이 아니면(다른 세션이 배포) **거부** + prod dist는 한 글자도 안 바뀐다.
#   · 스탬프 커밋이 로컬 저장소에 아예 없으면 **거부**(받아서 병합하라고 안내).
#   · 통과 시 prod에 심긴 스탬프의 commit = 배포한 쪽의 HEAD 전체 SHA.
#
# 실행: bash scripts/tests/safe_deploy_frontend_test.sh [대상 safe_deploy.sh 경로]
set -uo pipefail

SRC_SCRIPT="${1:-$(cd "$(dirname "$0")/.." && pwd)/safe_deploy.sh}"
ROOT=$(mktemp -d)
PASS=0; FAIL=0

check() { if [ "$2" = 0 ]; then echo "    ✓ $1"; PASS=$((PASS+1));
          else echo "    ✗ $1"; FAIL=$((FAIL+1)); fi; }
yes_() { "$@" >/dev/null 2>&1 && echo 0 || echo 1; }
no_()  { "$@" >/dev/null 2>&1 && echo 1 || echo 0; }
gc()   { git -c user.email=t@t -c user.name=t "$@"; }

setup() { # setup <이름>
  NAME="$1"
  REPO="$ROOT/$NAME/repo"; PROD="$ROOT/$NAME/prod"; BIN="$ROOT/$NAME/bin"
  mkdir -p "$REPO/scripts/tests" "$REPO/frontend/src" "$PROD/frontend/dist" "$BIN"
  cp "$SRC_SCRIPT" "$REPO/scripts/safe_deploy.sh"

  printf '// app v1\n' > "$REPO/frontend/src/App.tsx"
  ( cd "$REPO" && git init -q . && git add -A && gc commit -qm v1 )

  # 빌드 산출물(git 밖) — 매 배포 전에 "빌드했다" 치고 갈아끼운다.
  mkdir -p "$REPO/frontend/dist"
  echo "frontend/dist/" > "$REPO/.gitignore"
  ( cd "$REPO" && git add .gitignore && gc commit -qm gitignore )

  cat > "$BIN/ssh" <<EOF
#!/usr/bin/env bash
CMD="\${@: -1}"
bash -c "\$CMD"
EOF
  # rsync shim: -az --delete src/ host:dst/ → 로컬 디렉터리 복제
  cat > "$BIN/rsync" <<'EOF'
#!/usr/bin/env bash
args=(); for a in "$@"; do case "$a" in -*) ;; *) args+=("$a") ;; esac; done
SRC="${args[0]}"; DST="${args[1]#*:}"
mkdir -p "$DST"; rm -rf "${DST:?}/"* "${DST:?}"/.[!.]* 2>/dev/null
cp -R "$SRC". "$DST" 2>/dev/null || cp -R "$SRC"* "$DST"
EOF
  printf '#!/usr/bin/env bash\nshasum -a 256 "$@"\n' > "$BIN/sha256sum"
  chmod +x "$BIN/ssh" "$BIN/rsync" "$BIN/sha256sum"
}

build() { # build <내용>  — 빌드 산출물 갱신(스탬프는 스크립트가 심는다)
  printf '%s\n' "$1" > "$REPO/frontend/dist/index.html"
  printf '%s\n' "$1" > "$REPO/frontend/dist/app.js"
}
prod_build() { printf '%s\n' "$1" > "$PROD/frontend/dist/index.html"; }
prod_stamp() { printf 'commit=%s\nbranch=other\nts=x\n' "$1" > "$PROD/frontend/dist/.deploy-stamp"; }
prod_index() { cat "$PROD/frontend/dist/index.html" 2>/dev/null; }
prod_stamp_sha() { sed -n 's/^commit=//p' "$PROD/frontend/dist/.deploy-stamp" 2>/dev/null; }

run() {
  ( cd "$REPO" && PATH="$BIN:$PATH" SAFE_DEPLOY_HOST=fakehost SAFE_DEPLOY_REMOTE="$PROD" \
      bash scripts/safe_deploy.sh "$@" ) > "$ROOT/$NAME/out.log" 2>&1
  echo $?
}
LOG() { echo "$ROOT/$NAME/out.log"; }

# ══════════════════════════════════════════════════════════════════
echo "▶ F1: prod dist에 스탬프 없음(가드 도입 이전) → 통과 + 스탬프 심김"
setup f1; prod_build "old"; build "mine-v1"
RC=$(run --frontend)
check "exit 0"                    "$([ "$RC" = 0 ] && echo 0 || echo 1)"
check "부트스트랩 경고 출력"        "$(yes_ grep -q '스탬프 없음' "$(LOG)")"
check "prod dist 교체됨"           "$([ "$(prod_index)" = "mine-v1" ] && echo 0 || echo 1)"
check "스탬프 = 내 HEAD 전체 SHA"   "$([ "$(prod_stamp_sha)" = "$(cd "$REPO" && git rev-parse HEAD)" ] && echo 0 || echo 1)"

echo "▶ F2: prod 스탬프가 내 HEAD의 조상 → 통과(내 트리가 이미 포함)"
setup f2; prod_build "old"
BASE=$(cd "$REPO" && git rev-parse HEAD)
prod_stamp "$BASE"
( cd "$REPO" && printf '// app v2\n' > frontend/src/App.tsx && git add -A && gc commit -qm v2 )
build "mine-v2"
RC=$(run --frontend)
check "exit 0"           "$([ "$RC" = 0 ] && echo 0 || echo 1)"
check "prod dist 교체됨"  "$([ "$(prod_index)" = "mine-v2" ] && echo 0 || echo 1)"
check "스탬프 전진"       "$([ "$(prod_stamp_sha)" = "$(cd "$REPO" && git rev-parse HEAD)" ] && echo 0 || echo 1)"

echo "▶ F3: ★prod 스탬프가 내 역사에 없는 커밋(다른 세션 배포) → 거부 + prod 무변경"
setup f3; prod_build "theirs"
# 같은 저장소에 갈라진 브랜치를 만들어 '다른 세션의 커밋'을 준비한 뒤, 내 브랜치로 돌아온다.
( cd "$REPO" && git checkout -q -b other && printf '// their work\n' > frontend/src/Their.tsx \
  && git add -A && gc commit -qm theirs )
THEIRS=$(cd "$REPO" && git rev-parse other)
( cd "$REPO" && git checkout -q - )
prod_stamp "$THEIRS"
build "mine-v3"
RC=$(run --frontend)
check "exit != 0"                  "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "거부 사유에 커밋 표시"        "$(yes_ grep -q "$THEIRS" "$(LOG)")"
check "★prod dist 안 바뀜(덮지 않음)" "$([ "$(prod_index)" = "theirs" ] && echo 0 || echo 1)"
check "prod 스탬프 그대로"           "$([ "$(prod_stamp_sha)" = "$THEIRS" ] && echo 0 || echo 1)"
check "안내에 병합 절차 포함"        "$(yes_ grep -q 'git merge' "$(LOG)")"

echo "▶ F4: 스탬프 커밋이 로컬에 아예 없음(미공개 브랜치) → 거부"
setup f4; prod_build "unknown"
prod_stamp "0123456789abcdef0123456789abcdef01234567"
build "mine-v4"
RC=$(run --frontend)
check "exit != 0"                  "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "로컬에 없음 안내"            "$(yes_ grep -q '로컬에 없습니다' "$(LOG)")"
check "★prod dist 안 바뀜"          "$([ "$(prod_index)" = "unknown" ] && echo 0 || echo 1)"

echo "▶ F5: 커밋 안 된 frontend 변경 → 경고는 내되 배포는 진행"
setup f5; prod_build "old"; build "mine-v5"
( cd "$REPO" && printf '// uncommitted\n' >> frontend/src/App.tsx )
RC=$(run --frontend)
check "exit 0"              "$([ "$RC" = 0 ] && echo 0 || echo 1)"
check "커밋 안 된 변경 경고" "$(yes_ grep -q '커밋 안 된 변경' "$(LOG)")"

echo ""
echo "════════════ PASS=$PASS  FAIL=$FAIL ════════════"
echo "산출물: $ROOT"
[ "$FAIL" = 0 ]
