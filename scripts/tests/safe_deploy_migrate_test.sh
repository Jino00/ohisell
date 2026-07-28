#!/usr/bin/env bash
# safe_deploy.sh alembic 마이그레이션 가드 회귀 테스트 — ssh/scp/alembic를 shim으로
# 갈아끼워 prod를 안 건드리고 순서·거부 로직을 검증한다.
#
# ★왜 실행 가능한 테스트로 두는가: "마이그레이션은 코드보다 먼저"는 문서로는 못 지켜졌던
#   종류의 규칙이다(scripts/safe_deploy.sh 헤더의 2026-07-17/2026-07-28 사고 참조).
#   순서가 뒤집히면 여기서 빨간불이 난다.
#
# ★이 하니스가 검증하는 실제(main) 계약 — scripts/safe_deploy.sh 본문 기준:
#   · 트리거는 없다. FILES가 하나라도 있으면(그리고 --frontend가 아니면) 매번
#     prod의 `alembic current`/`heads`를 비교한다(models.py 유무와 무관).
#   · `--migrate` 를 줘야만 이 스크립트가 upgrade를 실행한다. 안 주면 prod가
#     head가 아닐 때 코드 배포/--restart 전부 거부된다. `--no-migrate` 플래그는 없다.
#   · 판정은 "로컬 head == prod alembic_version"이 아니라
#       ①커밋된 로컬 마이그 파일이 전부 prod 파일시스템에 있는가(없으면 배포 목록에
#         같이 있어야 함) + ②prod current == prod heads 인가.
#   · MIG_DIR="backend/alembic/versions" 아래 .py 파일만 alembic 자산으로 분류된다.
#   · 매니페스트 kind: 마이그 파일 전송="backend-migration", DB 적용="alembic"
#     (from/to 필드는 kind="alembic" 레코드에 flat하게 붙는다 — 중첩 객체 아님),
#     코드 파일 전송="backend".
#
# 실행: bash scripts/tests/safe_deploy_migrate_test.sh [대상 safe_deploy.sh 경로]
#      (backend/tests/test_safe_deploy_migrate.py 가 pytest에서도 이 파일을 돌린다)
# 각 시나리오마다 임시 git repo + 임시 "prod" 트리를 새로 만든다.
set -uo pipefail

SRC_SCRIPT="${1:-$(cd "$(dirname "$0")/.." && pwd)/safe_deploy.sh}"
ROOT=$(mktemp -d)
PASS=0; FAIL=0

check() { # check <설명> <결과(0=통과)>
  if [ "$2" = 0 ]; then echo "    ✓ $1"; PASS=$((PASS+1));
  else echo "    ✗ $1"; FAIL=$((FAIL+1)); fi
}
yes_() { "$@" >/dev/null 2>&1 && echo 0 || echo 1; }   # 성공해야 통과
no_()  { "$@" >/dev/null 2>&1 && echo 1 || echo 0; }   # 실패해야 통과

setup() { # setup <이름>
  NAME="$1"
  REPO="$ROOT/$NAME/repo"; PROD="$ROOT/$NAME/prod"; BIN="$ROOT/$NAME/bin"
  TRACE="$ROOT/$NAME/trace.log"
  mkdir -p "$REPO/scripts" "$REPO/backend/app" "$REPO/backend/alembic/versions" \
           "$PROD/backend/app" "$PROD/backend/alembic/versions" "$PROD/backend/.venv/bin" "$BIN"
  : > "$TRACE"
  cp "$SRC_SCRIPT" "$REPO/scripts/safe_deploy.sh"

  printf '# models v1\ncol_new = 1\n' > "$REPO/backend/app/models.py"
  printf '# routers v1\n'             > "$REPO/backend/app/routers.py"
  printf 'revision = "r001"\ndown_revision: Union[str, None] = None\n' \
      > "$REPO/backend/alembic/versions/aaa_first.py"

  # prod 초기 상태 = repo 첫 커밋 내용(CAS가 "내 역사 속 버전"을 요구하므로).
  ( cd "$REPO" && git init -q . && git add -A && git -c user.email=t@t -c user.name=t commit -qm v1 )
  cp "$REPO/backend/app/models.py" "$REPO/backend/app/routers.py" "$PROD/backend/app/"
  cp "$REPO/backend/alembic/versions/aaa_first.py" "$PROD/backend/alembic/versions/"
  echo "r001" > "$PROD/backend/.alembic_state"

  # alembic shim: current/heads는 읽기 전용(prod 파일시스템의 리비전 그래프에서 계산),
  # upgrade만 .alembic_state를 바꾼다. 여러 head(분기)면 진짜 alembic처럼 upgrade를 거부한다.
  cat > "$PROD/backend/.venv/bin/alembic" <<EOF
#!/usr/bin/env bash
STATE="$PROD/backend/.alembic_state"
VDIR="$PROD/backend/alembic/versions"
leaf_heads() {
  local R D
  R=\$(grep -hE '^revision[^=]*=' "\$VDIR"/*.py 2>/dev/null | sed -E "s/^[^=]*=[[:space:]]*//" | tr -d "\"'" | awk '{print \$1}' | sort -u)
  D=\$(grep -hE '^down_revision[^=]*=' "\$VDIR"/*.py 2>/dev/null | sed -E "s/^[^=]*=[[:space:]]*//" | tr -d "\"'" | awk '{print \$1}' | grep -v '^None\$' | sort -u)
  comm -23 <(echo "\$R") <(echo "\$D") | sed '/^\$/d'
}
case "\$1" in
  current)
    [ -s "\$STATE" ] && echo "\$(cat "\$STATE") (current)"
    ;;
  heads)
    leaf_heads | sed 's/\$/ (head)/'
    ;;
  upgrade)
    echo "ALEMBIC upgrade" >> "$TRACE"
    if [ -n "\${ALEMBIC_FAIL:-}" ]; then echo "boom" >&2; exit 1; fi
    if [ -n "\${ALEMBIC_NOOP:-}" ]; then exit 0; fi
    NEWHEADS=\$(leaf_heads)
    N=\$(echo "\$NEWHEADS" | grep -c .)
    if [ "\$N" -gt 1 ]; then
      echo "FAILED: Multiple head revisions are present" >&2
      exit 1
    fi
    echo "\$NEWHEADS" > "\$STATE"
    echo "INFO upgraded to \$(cat "\$STATE")"
    ;;
esac
EOF
  chmod +x "$PROD/backend/.venv/bin/alembic"

  cat > "$BIN/ssh" <<EOF
#!/usr/bin/env bash
CMD="\${@: -1}"
echo "SSH \$CMD" >> "$TRACE"
bash -c "\$CMD"
EOF
  cat > "$BIN/scp" <<EOF
#!/usr/bin/env bash
args=(); for a in "\$@"; do [ "\$a" = "-q" ] || args+=("\$a"); done
SRC="\${args[0]}"; DST="\${args[1]#*:}"
echo "SCP \$SRC" >> "$TRACE"
mkdir -p "\$(dirname "\$DST")"; cp "\$SRC" "\$DST"
EOF
  printf '#!/usr/bin/env bash\nshasum -a 256 "$@"\n' > "$BIN/sha256sum"
  chmod +x "$BIN/ssh" "$BIN/scp" "$BIN/sha256sum"
}

commit_v2() { ( cd "$REPO" && git add -A && git -c user.email=t@t -c user.name=t commit -qm v2 ); }
add_rev2() { printf "revision = \"r002\"\ndown_revision: Union[str, None] = 'r001'\n" \
                 > "$REPO/backend/alembic/versions/bbb_second.py"; }
touch_models() { printf '# models v2\n' >> "$REPO/backend/app/models.py"; }

run() {
  ( cd "$REPO" && PATH="$BIN:$PATH" SAFE_DEPLOY_HOST=fakehost SAFE_DEPLOY_REMOTE="$PROD" \
      bash scripts/safe_deploy.sh "$@" ) > "$ROOT/$NAME/out.log" 2>&1
  echo $?
}
LOG() { echo "$ROOT/$NAME/out.log"; }
before() { # TRACE에서 $1이 $2보다 먼저 나오는가
  local a b; a=$(grep -n "$1" "$TRACE" | head -1 | cut -d: -f1); b=$(grep -n "$2" "$TRACE" | head -1 | cut -d: -f1)
  [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]; }
schema() { cat "$PROD/backend/.alembic_state" 2>/dev/null; }

# ══════════════════════════════════════════════════════════════════
echo "▶ S1: 마이그레이션 대기 아님 → 평범한 배포는 회귀 없음(alembic upgrade 미호출)"
setup s1; printf '# routers v2\n' > "$REPO/backend/app/routers.py"; commit_v2
RC=$(run backend/app/routers.py)
check "exit 0"            "$([ "$RC" = 0 ] && echo 0 || echo 1)"
check "alembic upgrade 미호출" "$(no_ grep -q 'ALEMBIC' "$TRACE")"
check "routers.py 반영됨"  "$(yes_ grep -q 'routers v2' "$PROD/backend/app/routers.py")"

echo "▶ S2: models.py + 신규 리비전 + --migrate → 리비전 먼저 전송 → upgrade → 앱 코드 전송"
setup s2; add_rev2; touch_models; commit_v2
RC=$(run backend/app/models.py backend/alembic/versions/bbb_second.py --migrate)
check "exit 0"                          "$([ "$RC" = 0 ] && echo 0 || echo 1)"
check "리비전 전송 < upgrade"            "$(yes_ before 'SCP.*bbb_second' 'ALEMBIC upgrade')"
check "upgrade < models.py 전송(★선행)"  "$(yes_ before 'ALEMBIC upgrade' 'SCP.*models.py')"
check "prod 스키마 r002"                 "$([ "$(schema)" = r002 ] && echo 0 || echo 1)"
check "manifest에 마이그 파일 전송 기록(kind=backend-migration)" \
      "$(yes_ grep -q '\"kind\":\"backend-migration\"' "$PROD/deploy-manifest.jsonl")"
check "manifest에 alembic 적용 기록(kind=alembic, from/to)" \
      "$(yes_ grep -q '\"kind\":\"alembic\".*\"from\":\"r001\".*\"to\":\"r002\"' "$PROD/deploy-manifest.jsonl")"

echo "▶ S3: 리비전 파일이 prod에도 배포목록에도 없음 → 코드 우선 배포 차단"
setup s3; add_rev2; touch_models; commit_v2
RC=$(run backend/app/models.py --migrate)
check "exit != 0"              "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "누락 파일명 안내"        "$(yes_ grep -q 'bbb_second' "$(LOG)")"
check "누락 사유 메시지"        "$(yes_ grep -q '마이그레이션 파일 누락' "$(LOG)")"
check "alembic 미실행"          "$(no_ grep -q 'ALEMBIC' "$TRACE")"
check "prod models.py 무변경"   "$(no_ grep -q 'models v2' "$PROD/backend/app/models.py")"

echo "▶ S4: --migrate 중 alembic upgrade 실패 → 앱 코드 미전송(구코드+구스키마 정합)"
setup s4; add_rev2; touch_models; commit_v2
RC=$(ALEMBIC_FAIL=1 run backend/app/models.py backend/alembic/versions/bbb_second.py --migrate)
check "exit != 0"             "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "실패 안내 메시지"       "$(yes_ grep -q '원격 alembic upgrade head 실패' "$(LOG)")"
check "prod models.py 무변경" "$(no_ grep -q 'models v2' "$PROD/backend/app/models.py")"
check "스키마 r001 유지"       "$([ "$(schema)" = r001 ] && echo 0 || echo 1)"
check "배포 락 해제됨"         "$([ ! -d "$PROD/.deploy-lock" ] && echo 0 || echo 1)"

echo "▶ S5: --migrate 중 upgrade가 조용히 no-op → head 불일치로 중단"
setup s5; add_rev2; touch_models; commit_v2
RC=$(ALEMBIC_NOOP=1 run backend/app/models.py backend/alembic/versions/bbb_second.py --migrate)
check "exit != 0"             "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "head 불일치 메시지"     "$(yes_ grep -q 'head 불일치' "$(LOG)")"
check "prod models.py 무변경" "$(no_ grep -q 'models v2' "$PROD/backend/app/models.py")"

echo "▶ S6: 마이그 대기 상태에서 --migrate 없이 코드 파일 배포 시도 → 거부"
setup s6
add_rev2  # 로컬 커밋에는 반영하지만 배포 목록엔 안 올린다
cp "$REPO/backend/alembic/versions/bbb_second.py" "$PROD/backend/alembic/versions/"  # 파일은 이미 prod에 있음(선행 배포 가정), 스키마만 미적용
touch_models; commit_v2
RC=$(run backend/app/models.py)
check "exit != 0"              "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "마이그레이션 미적용 메시지" "$(yes_ grep -q '마이그레이션 미적용' "$(LOG)")"
check "alembic upgrade 미호출"  "$(no_ grep -q 'ALEMBIC' "$TRACE")"
check "prod models.py 무변경"   "$(no_ grep -q 'models v2' "$PROD/backend/app/models.py")"
check "스키마 r001 유지"        "$([ "$(schema)" = r001 ] && echo 0 || echo 1)"

echo "▶ S7: 마이그 대기 상태에서 --migrate 없이 --restart 시도 → 거부(재시작 미실행)"
setup s7
add_rev2
cp "$REPO/backend/alembic/versions/bbb_second.py" "$PROD/backend/alembic/versions/"
commit_v2   # models.py는 안 건드림 — 이미 prod와 동일한 파일로 --restart만 시도
RC=$(run backend/app/routers.py --restart)
check "exit != 0"              "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "마이그레이션 미적용 메시지" "$(yes_ grep -q '마이그레이션 미적용' "$(LOG)")"
check "재시작 미실행"           "$(no_ grep -q 'pm2' "$TRACE")"
check "alembic upgrade 미호출"  "$(no_ grep -q 'ALEMBIC' "$TRACE")"

echo "▶ S8: 파일은 전부 최신인데 스키마만 뒤처짐 + --migrate → 재실행이 스스로 치유"
setup s8; add_rev2; commit_v2
cp "$REPO/backend/app/models.py" "$PROD/backend/app/models.py"
cp "$REPO/backend/alembic/versions/bbb_second.py" "$PROD/backend/alembic/versions/"
RC=$(run backend/app/models.py --migrate)
check "exit 0"                    "$([ "$RC" = 0 ] && echo 0 || echo 1)"
check "'이미 배포됨' 인식"         "$(yes_ grep -q '이미 배포됨' "$(LOG)")"
check "그래도 스키마 r002로 치유"  "$([ "$(schema)" = r002 ] && echo 0 || echo 1)"

echo "▶ S9: repo head 2개(분기) → --migrate 없이 거부(comma-join heads 파싱 확인)"
setup s9; add_rev2
printf "revision = \"r003\"\ndown_revision: Union[str, None] = 'r001'\n" > "$REPO/backend/alembic/versions/ccc_branch.py"
touch_models; commit_v2
RC=$(run backend/app/models.py backend/alembic/versions/bbb_second.py backend/alembic/versions/ccc_branch.py)
check "exit != 0"       "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "마이그레이션 미적용 메시지" "$(yes_ grep -q '마이그레이션 미적용' "$(LOG)")"
check "alembic upgrade 미실행"   "$(no_ grep -q 'ALEMBIC' "$TRACE")"

echo "▶ S10: CAS 실패(타 세션 배포분) 규칙은 마이그 가드 도입 후에도 그대로 작동"
setup s10; touch_models; commit_v2
printf '# 다른 세션이 배포한 미지의 내용\n' > "$PROD/backend/app/models.py"
RC=$(run backend/app/models.py)
check "exit != 0"        "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "CAS 실패 메시지"   "$(yes_ grep -q 'CAS 실패' "$(LOG)")"
check "alembic upgrade 미실행" "$(no_ grep -q 'ALEMBIC' "$TRACE")"

echo "▶ S11: 미커밋 변경 거부 규칙 유지"
setup s11; touch_models   # 커밋하지 않음
RC=$(run backend/app/models.py)
check "exit != 0"          "$([ "$RC" != 0 ] && echo 0 || echo 1)"
check "커밋 안 된 변경 안내" "$(yes_ grep -q '커밋 안 된 변경' "$(LOG)")"

echo ""
echo "════════════ PASS=$PASS  FAIL=$FAIL ════════════"
echo "산출물: $ROOT"
[ "$FAIL" = 0 ]
