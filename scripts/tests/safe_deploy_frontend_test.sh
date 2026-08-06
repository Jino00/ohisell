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

build() { # build <내용>  — 빌드 산출물 갱신 + 빌드 스탬프(실제 npm run build가 하는 일)
  printf '%s\n' "$1" > "$REPO/frontend/dist/index.html"
  printf '%s\n' "$1" > "$REPO/frontend/dist/app.js"
  local D; D=$( cd "$REPO" && [ -z "$(git status --porcelain -- frontend)" ] && echo 0 || echo 1 )
  printf 'commit=%s\ndirty=%s\nbuilt_at=x\n' "$(cd "$REPO" && git rev-parse HEAD)" "$D" \
    > "$REPO/frontend/dist/.build-stamp"
}
build_at() { # build_at <내용> <커밋SHA> — 옛 커밋에서 빌드된 stale dist를 흉내
  printf '%s\n' "$1" > "$REPO/frontend/dist/index.html"
  printf 'commit=%s\ndirty=0\nbuilt_at=x\n' "$2" > "$REPO/frontend/dist/.build-stamp"
}
prod_build() { printf '%s\n' "$1" > "$PROD/frontend/dist/index.html"; }
prod_stamp() { printf 'commit=%s\nbranch=other\nts=x\n' "$1" > "$PROD/.frontend-deploy-stamp"; }
prod_stamp_raw() { printf '%s\n' "$1" > "$PROD/.frontend-deploy-stamp"; }
prod_index() { cat "$PROD/frontend/dist/index.html" 2>/dev/null; }
prod_stamp_sha() { sed -n 's/^commit=//p' "$PROD/.frontend-deploy-stamp" 2>/dev/null; }

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

echo "▶ F5: 빌드 후에 소스를 고친 경우 → 통과(그 변경은 dist에 없다 — 스탬프가 정직하다)"
setup f5; prod_build "old"; build "mine-v5"      # 깨끗한 상태에서 빌드(dirty=0)
( cd "$REPO" && printf '// 빌드 이후 수정\n' >> frontend/src/App.tsx )
RC=$(run --frontend)
check "exit 0"           "$([ "$RC" = 0 ] && echo 0 || echo 1)"
check "prod=빌드 시점 내용" "$([ "$(prod_index)" = "mine-v5" ] && echo 0 || echo 1)"

echo "▶ F6: ★같은 커밋에서 미커밋 변경으로 빌드 → 거부(두 세션의 스탬프가 같아 구분 불가)"
setup f6; prod_build "old"; build "mine-v6"
( cd "$REPO" && printf '// uncommitted\n' >> frontend/src/App.tsx )
build "mine-v6-dirty"     # dirty=1 로 다시 스탬프
RC=$(run --frontend)
check "exit != 0"            "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "미커밋 사유 안내"      "$(yes_ grep -q '커밋 안 된 변경' "$(LOG)")"
check "★prod 안 바뀜"        "$([ "$(prod_index)" = "old" ] && echo 0 || echo 1)"
RC=$(run --frontend --force-frontend)
check "--force-frontend로는 통과" "$([ "$RC" = 0 ] && echo 0 || echo 1)"
check "매니페스트에 forced 기록"  "$(yes_ grep -q '"forced":true' "$PROD/deploy-manifest.jsonl")"

echo "▶ F7: ★병합만 하고 재빌드 안 함(stale dist) → 거부 (리뷰 P1-2)"
setup f7; prod_build "theirs"
OLD=$(cd "$REPO" && git rev-parse HEAD)
( cd "$REPO" && printf '// v7\n' > frontend/src/App.tsx && git add -A && gc commit -qm v7 )
prod_stamp "$OLD"                 # prod는 내 조상에서 배포됨 = CAS는 통과할 상황
build_at "STALE-BUILT-BEFORE-MERGE" "$OLD"   # dist만 옛 커밋 것
RC=$(run --frontend)
check "exit != 0"                  "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "재빌드 요구 안내"            "$(yes_ grep -q '재빌드' "$(LOG)")"
check "★prod 안 바뀜(손실 없음)"    "$([ "$(prod_index)" = "theirs" ] && echo 0 || echo 1)"
check "★스탬프 전진 안 함(증거 보존)" "$([ "$(prod_stamp_sha)" = "$OLD" ] && echo 0 || echo 1)"

echo "▶ F8: ★스탬프 손상 → 통과가 아니라 거부(fail-closed, 리뷰 P1-3)"
setup f8; prod_build "theirs"; build "mine-v8"
prod_stamp_raw "commit=abc12"
RC=$(run --frontend)
check "exit != 0"          "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "손상 안내"           "$(yes_ grep -q '해석할 수 없습니다' "$(LOG)")"
check "★prod 안 바뀜"      "$([ "$(prod_index)" = "theirs" ] && echo 0 || echo 1)"

echo "▶ F9: ★스탬프 읽기 ssh 실패 → 거부(없음으로 오인하지 않는다)"
setup f9; prod_build "theirs"; build "mine-v9"
prod_stamp "$(cd "$REPO" && git rev-parse HEAD)"
# 스탬프 읽기 ssh만 죽인다(락 획득 등 다른 ssh는 살려야 이 경로를 격리할 수 있다).
cat > "$BIN/ssh" <<EOF
#!/usr/bin/env bash
CMD="\${@: -1}"
case "\$CMD" in *frontend-deploy-stamp*) exit 255 ;; esac
bash -c "\$CMD"
EOF
chmod +x "$BIN/ssh"
RC=$(run --frontend)
check "exit != 0"            "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "ssh 실패 안내"         "$(yes_ grep -q '읽지 못했습니다' "$(LOG)")"
check "★prod 안 바뀜"        "$([ "$(prod_index)" = "theirs" ] && echo 0 || echo 1)"

echo "▶ F10: 빌드 스탬프 없는 구버전 dist → 거부(재빌드 요구)"
setup f10; prod_build "old"
printf 'no-stamp\n' > "$REPO/frontend/dist/index.html"
RC=$(run --frontend)
check "exit != 0"        "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "재빌드 안내"       "$(yes_ grep -q 'build-stamp 없음' "$(LOG)")"
check "★prod 안 바뀜"    "$([ "$(prod_index)" = "old" ] && echo 0 || echo 1)"

echo "▶ F11: 구버전 스크립트가 dist를 덮어도 원격 스탬프는 살아남는다(리뷰 P1-4)"
setup f11; prod_build "theirs"
THEIRS2=$(cd "$REPO" && git rev-parse HEAD)
prod_stamp "$THEIRS2"
# 구버전 스크립트 흉내 = dist만 통짜 교체(스탬프 파일 없음)
rm -rf "$PROD/frontend/dist"; mkdir -p "$PROD/frontend/dist"
printf 'legacy-deploy\n' > "$PROD/frontend/dist/index.html"
check "★원격 스탬프 생존(dist 밖)" "$([ "$(prod_stamp_sha)" = "$THEIRS2" ] && echo 0 || echo 1)"

echo ""
echo "════════════ PASS=$PASS  FAIL=$FAIL ════════════"
echo "산출물: $ROOT"
[ "$FAIL" = 0 ]
