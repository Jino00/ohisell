#!/usr/bin/env bash
# safe_deploy.sh — prod 배포 CAS(compare-and-swap) 가드 (D-NAO-49)
#                  + alembic 마이그레이션 순서 가드 (2026-07-28)
#
# ★존재 이유(2026-07-17 사고): 병행 세션 A가 07:35에 entity_sync.py(qi 수집)를 prod에
# 배포했고, 세션 B(나)가 07:41에 구 base 기준 같은 파일을 scp로 덮어 **A의 기능이 4분
# 만에 죽었다**. 원칙20("트랙 동시 작업 금지")은 문서라 세 번 다 못 막았다 — 구조로 막는다.
#
# ★왜 "origin/main 병합했나" 체크로는 안 되는가: 이 프로젝트 관례가 **배포 → 나중에 PR**
# 이라(양쪽 다 그랬다), 충돌 시점엔 상대 코드가 어느 공유 ref에도 없다. ref 수준 체크는
# 원리적으로 이 사고를 못 잡는다.
#
# ★유일하게 잡는 규칙(이 스크립트의 핵심):
#   덮으려는 prod 파일의 현재 내용이 **내 브랜치 역사(git rev-list HEAD)에 존재하는
#   버전**이어야 한다. 내 브랜치가 한 번도 본 적 없는 내용이 prod에 있다
#   = 다른 세션이 배포했다 = **즉시 중단**(병합 후 재시도).
#
# ★마이그레이션 순서 가드(2026-07-28, rocket-1p 리뷰에서 실증):
#   이 저장소는 **인프로세스 마이그레이션을 하지 않는다**(app/main.py 부팅 시 upgrade 없음,
#   alembic/env.py 주석 참조). 그래서 `models.py`를 마이그레이션보다 **먼저** 배포하면,
#   nullable 컬럼 추가처럼 무해해 보이는 변경이라도 SQLAlchemy ORM이 엔티티를 통째로
#   SELECT 하면서 `OperationalError: no such column` 을 던져 **그 테이블의 ingest 경로가
#   통째로 죽는다**(신규 필드만 죽는 게 아니다).
#     실측(커밋 85967cf): `settlement ingest FAIL: no such column:
#     coupang_rocket_settlement.tax_invoice_transmitted` /
#     `po_items ingest FAIL: no such column:
#     coupang_rocket_purchase_order_item.vendor_confirmed_qty`
#   이 저장소엔 "조용한 수집 침묵" 사고 이력이 있다(RG 26일, 쿠팡 광고비 크론). 순서를
#   docstring/HANDOFF에 적어두는 방식은 이미 여러 번 실패했으므로 여기서 구조로 막는다.
#   강제하는 순서: **①마이그레이션 파일 배포 → ②원격 alembic upgrade head → ③코드 배포 → ④재시작**
#   · 마이그레이션 대기 상태에서 코드 파일 배포/재시작을 시도하면 **거부**한다.
#   · `--migrate` 를 주면 ②를 이 스크립트가 대신 실행한다(주지 않으면 안내만 하고 중단).
#   · 로컬(커밋된) 마이그레이션 파일이 prod에 없는데 배포 목록에도 없으면 **거부**한다.
#   · ※이 순서는 additive(컬럼/테이블 추가) 기준이다. 컬럼 삭제처럼 구코드를 깨는
#     마이그레이션은 순서가 반대여야 하므로 --migrate 쓰지 말고 수동으로 조율할 것.
#   · ※`--migrate` 는 prod DB를 변경한다. 실행 전 DB 백업 여부는 운영자 판단.
#
# ★프론트 스탬프 CAS(2026-08-06 사고): `--frontend` 는 dist를 통짜로 rsync 하므로 파일 단위
#   CAS가 안 걸린다. 그 틈으로 병행 세션이 09:09·09:23 두 번 서로의 프론트 수정을 조용히
#   지웠다. 이제 dist에 `.deploy-stamp`(빌드 커밋)를 심고, 배포 전 prod 스탬프가 **내 역사의
#   조상**이 아니면 거부한다. 상세는 아래 frontend 블록 주석 참조.
#
# 추가 안전장치:
#   · prod 측 배포 락(mkdir 원자성) — 두 세션 동시 배포 자체를 차단
#   · 커밋 안 된 변경이 있는 파일은 배포 거부(배포물 = 커밋된 내용, 재현 가능)
#   · 배포 후 sha 대조 + prod 매니페스트(deploy-manifest.jsonl)에 who/when/commit 기록
#
# ★재시작은 무중단이 기본(2026-08-05): `--restart` 는 블루-그린으로 동작한다
#   (scripts/zero_downtime_restart.sh). 구 `pm2 restart` 는 콜드부팅 ~50초 동안 전 요청을
#   502로 만들었고, 그 창에서 갱신 버튼 POST·Mac 페처 push가 유실됐다(3일 61회).
#   무중단 경로가 고장난 비상시에만 `--restart-legacy`(다운타임 감수).
#
# 사용:
#   scripts/safe_deploy.sh backend/app/routers/naver_ad.py [파일...] [--restart]
#   scripts/safe_deploy.sh backend/alembic/versions/xxx.py backend/app/models.py --migrate --restart
#   scripts/safe_deploy.sh --frontend            # frontend/dist rsync(락+스탬프 CAS+매니페스트)
#   scripts/safe_deploy.sh ... --steal-lock      # 죽은 세션의 락 강제 해제(사유 확인 후)
set -euo pipefail

# 테스트용 오버라이드 허용(실 prod 안 건드리고 실패 경로 검증) — 평소엔 건드리지 말 것.
HOST="${SAFE_DEPLOY_HOST:-sellc.ohitech.co.kr}"
REMOTE_REPO="${SAFE_DEPLOY_REMOTE:-/home/ubuntu/ohisell}"   # repo-relative 경로가 그대로 매핑됨
LOCK_DIR="$REMOTE_REPO/.deploy-lock"
MANIFEST="$REMOTE_REPO/deploy-manifest.jsonl"
MIG_DIR="backend/alembic/versions"                          # repo-relative
REMOTE_ALEMBIC="${SAFE_DEPLOY_ALEMBIC:-.venv/bin/alembic}"  # $REMOTE_REPO/backend 기준

RESTART=0; RESTART_LEGACY=0; FRONTEND=0; STEAL=0; MIGRATE=0; FORCE_FE=0; FILES=()
for a in "$@"; do
  case "$a" in
    --restart) RESTART=1 ;;
    --restart-legacy) RESTART_LEGACY=1 ;;
    --frontend) FRONTEND=1 ;;
    --force-frontend) FORCE_FE=1 ;;
    --steal-lock) STEAL=1 ;;
    --migrate) MIGRATE=1 ;;
    -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
    *) FILES+=("$a") ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"
BRANCH=$(git branch --show-current)
COMMIT=$(git rev-parse --short HEAD)

fail() { echo "❌ ABORT: $1" >&2; exit 1; }

# ── 0. 배포 락 (prod 측, mkdir 원자성) ──────────────────────────────
release_lock() { ssh -o BatchMode=yes "$HOST" "rm -rf '$LOCK_DIR'" 2>/dev/null || true; }
if [ "$STEAL" = 1 ]; then
  echo "⚠️ 락 강제 해제 — 기존 소유자:"
  ssh -o BatchMode=yes "$HOST" "cat '$LOCK_DIR/owner' 2>/dev/null || echo '(없음)'"
  release_lock
fi
if ! ssh -o BatchMode=yes "$HOST" "mkdir '$LOCK_DIR' 2>/dev/null"; then
  echo "── 락 소유자 ──" >&2
  ssh -o BatchMode=yes "$HOST" "cat '$LOCK_DIR/owner' 2>/dev/null" >&2 || true
  fail "다른 배포가 진행 중(락 존재). 끝나길 기다리거나, 죽은 세션이 확실하면 --steal-lock."
fi
trap release_lock EXIT
ssh -o BatchMode=yes "$HOST" "printf '%s\n' 'branch=$BRANCH commit=$COMMIT host=$(hostname -s) ts=$(date -u +%FT%TZ)' > '$LOCK_DIR/owner'"

# ── frontend 모드 ────────────────────────────────────────────────────
# ★스탬프 CAS(2026-08-06 사고): dist는 통짜 rsync라 **파일 단위** CAS를 못 건다. 그래서
#   같은 날 09:09·09:23 두 번, 병행 세션이 서로의 프론트 수정을 조용히 지웠다(백엔드였다면
#   CAS가 막았을 상황이다. 09:23~09:24 약 1분간 그날 고친 것들이 prod에서 사라져 있었다).
#   해법: dist **내용물**은 git 역사에 없지만 "어느 커밋에서 나왔는지"는 남길 수 있다.
#   배포 때 dist에 커밋 스탬프를 심고, 다음 배포 전에 prod 스탬프가 **내 역사의 조상**인지
#   검사한다. 조상이면 내 트리가 그 작업을 이미 포함한다 = 덮어도 안전. 아니면 = 내가 모르는
#   배포가 있었다 = 거부. (백엔드 CAS의 "내 브랜치 역사에 있는 버전인가"와 같은 판정이다.)
if [ "$FRONTEND" = 1 ]; then
  [ -d frontend/dist ] || fail "frontend/dist 없음 — 먼저 npm run build"
  if [ "$RESTART" = 1 ] || [ "$RESTART_LEGACY" = 1 ]; then
    echo "⚠️ --frontend 에는 재시작이 필요 없습니다(정적 자산) — --restart 무시합니다."
  fi
  # 원격 스탬프는 **dist 밖**에 둔다. dist 안에 두면 스탬프를 심지 않는 구버전 스크립트가
  # 한 번만 배포해도 `rsync --delete`가 스탬프를 지워 가드가 영구 무장해제된다(리뷰 P1-4).
  R_STAMP_PATH="$REMOTE_REPO/.frontend-deploy-stamp"
  LEGACY_R_STAMP_PATH="$REMOTE_REPO/frontend/dist/.deploy-stamp"
  BUILD_STAMP="frontend/dist/.build-stamp"
  COMMIT_FULL=$(git rev-parse HEAD)
  ABSENT="__ABSENT__"

  parse_commit() { sed -n 's/^commit=\([0-9a-f]\{40\}\)$/\1/p' | sed -n '1{p;q;}'; }

  # ── ① 로컬 dist가 **지금 이 커밋에서 빌드된 것**인가 (리뷰 P1-1·P1-2) ──────────
  # 빌드가 심는 스탬프라 "주장"이 아니라 사실이다. 이게 없으면 재빌드를 문서로만 요구하게 되고,
  # 그 방식은 이 저장소에서 이미 세 번 실패했다(스크립트 헤더 참조).
  [ -f "$BUILD_STAMP" ] || fail "frontend/dist/.build-stamp 없음 — 구버전 빌드입니다.
    재빌드하세요: (cd frontend && npm run build)"
  B_SHA=$(parse_commit < "$BUILD_STAMP")
  B_DIRTY=$(sed -n 's/^dirty=\([01]\)$/\1/p' "$BUILD_STAMP" | sed -n '1{p;q;}')
  [ -n "$B_SHA" ] || fail "빌드 스탬프를 읽을 수 없습니다($BUILD_STAMP) — 재빌드하세요."
  if [ "$B_SHA" != "$COMMIT_FULL" ]; then
    fail "dist가 현재 커밋에서 빌드되지 않았습니다.
      dist 빌드 커밋: $B_SHA
      현재 HEAD     : $COMMIT_FULL
    병합만 하고 옛 dist를 올리면 상대 작업이 그대로 사라집니다(스탬프만 최신이 되어 더 나쁩니다).
    재빌드하세요: (cd frontend && npm run build)"
  fi
  if [ "$B_DIRTY" = 1 ] && [ "$FORCE_FE" != 1 ]; then
    fail "frontend/ 에 커밋 안 된 변경이 있는 상태로 빌드됐습니다.
    이때는 두 세션의 스탬프가 **같은 커밋**이 되어 가드가 서로를 구분하지 못합니다(리뷰 P1-1).
    커밋한 뒤 재빌드하세요. 불가피하면 --force-frontend (매니페스트에 기록됩니다)."
  fi

  # ── ② prod 스탬프 대조 — 읽기/파싱 실패는 통과가 아니라 거부(fail-closed, 리뷰 P1-3) ──
  R_RAW=$(ssh -o BatchMode=yes "$HOST" \
    "if [ -f '$R_STAMP_PATH' ]; then cat '$R_STAMP_PATH'; \
     elif [ -f '$LEGACY_R_STAMP_PATH' ]; then cat '$LEGACY_R_STAMP_PATH'; \
     else echo '$ABSENT'; fi") || fail "prod 스탬프를 읽지 못했습니다(ssh 실패).
    네트워크/권한을 확인하고 다시 시도하세요 — 읽지 못한 것을 '없음'으로 처리하지 않습니다."
  if [ "$R_RAW" = "$ABSENT" ]; then
    echo "⚠️ prod에 스탬프 없음(가드 도입 이전 배포) — 이번 1회 통과, 스탬프를 심습니다."
  else
    R_SHA=$(printf '%s\n' "$R_RAW" | parse_commit)
    if [ -z "$R_SHA" ]; then
      echo "── prod 스탬프 원문 ──" >&2; printf '%s\n' "$R_RAW" >&2
      fail "prod 스탬프를 해석할 수 없습니다(손상). 통과시키지 않습니다 — 내용을 확인하고,
    의도적으로 넘기려면 --force-frontend 를 쓰세요."
    fi
    if ! git cat-file -e "${R_SHA}^{commit}" 2>/dev/null; then
      git fetch --all --quiet 2>/dev/null || true
    fi
    if ! git cat-file -e "${R_SHA}^{commit}" 2>/dev/null; then
      [ "$FORCE_FE" = 1 ] || fail "prod를 배포한 커밋 $R_SHA 가 로컬에 없습니다(다른 세션의 미공개 브랜치).
    그 세션의 작업을 받아 병합한 뒤 **재빌드**하고 다시 시도하세요."
    elif ! git merge-base --is-ancestor "$R_SHA" HEAD; then
      [ "$FORCE_FE" = 1 ] || fail "prod가 내 역사에 없는 커밋($R_SHA)에서 배포됐습니다.
    ① 다른 세션이 배포한 경우(대부분): 받아서 합친 뒤 **재빌드**하고 재시도
         git fetch origin && git merge origin/main
         (cd frontend && npm run build)
         scripts/safe_deploy.sh --frontend
    ② 내가 amend/rebase 해서 옛 커밋이 역사에서 빠진 경우: 위 절차로는 해소되지 않습니다.
       prod 내용이 내 것임을 확인했다면 --force-frontend (매니페스트에 기록됩니다)."
    fi
  fi
  [ "$FORCE_FE" != 1 ] || echo "⚠️ --force-frontend — 스탬프 검사를 건너뜁니다(매니페스트에 기록)."

  STAMP=$(date +%Y%m%d_%H%M)
  ssh -o BatchMode=yes "$HOST" "cp -r '$REMOTE_REPO/frontend/dist' '$REMOTE_REPO/frontend/dist_backup_$STAMP'"
  if ! rsync -az --delete frontend/dist/ "$HOST:$REMOTE_REPO/frontend/dist/"; then
    fail "rsync 실패 — prod dist가 중간 상태일 수 있습니다.
    복구: ssh $HOST \"rm -rf $REMOTE_REPO/frontend/dist && mv $REMOTE_REPO/frontend/dist_backup_$STAMP $REMOTE_REPO/frontend/dist\""
  fi
  L=$(shasum -a 256 frontend/dist/index.html | cut -d' ' -f1)
  R=$(ssh -o BatchMode=yes "$HOST" "sha256sum '$REMOTE_REPO/frontend/dist/index.html' | cut -d' ' -f1")
  [ "$L" = "$R" ] || fail "index.html sha 불일치"
  # 스탬프는 rsync **후**에, dist 밖에 쓴다. detached HEAD면 branch가 비므로 대체값을 넣는다.
  BRANCH_LABEL="${BRANCH:-detached@$COMMIT}"
  ssh -o BatchMode=yes "$HOST" "printf 'commit=%s\nbranch=%s\nts=%s\n' '$COMMIT_FULL' '$BRANCH_LABEL' '$(date -u +%FT%TZ)' > '$R_STAMP_PATH'"
  ssh -o BatchMode=yes "$HOST" "printf '%s\n' '{\"ts\":\"$(date -u +%FT%TZ)\",\"kind\":\"frontend\",\"branch\":\"$BRANCH_LABEL\",\"commit\":\"$COMMIT\",\"backup\":\"dist_backup_$STAMP\",\"forced\":$([ "$FORCE_FE" = 1 ] && echo true || echo false)}' >> '$MANIFEST'"
  echo "✅ frontend 배포 완료 (백업: dist_backup_$STAMP)"
  exit 0
fi

[ ${#FILES[@]} -gt 0 ] || fail "배포할 파일을 지정하세요 (repo-relative 경로)"

# ── 배치 배포 함수: CAS 사전검사 → 전송 → sha 검증 → 매니페스트 ────────
#    사용: deploy_batch <kind> <파일...>   (kind = 매니페스트의 "kind" 필드)
deploy_batch() {
  local kind="$1"; shift
  local f RTMP LOCAL_BLOB REMOTE_BLOB KNOWN c L R FILES_JSON
  local -a TO_SEND=()
  [ $# -gt 0 ] || return 0

  for f in "$@"; do
    [ -f "$f" ] || fail "$f — 로컬에 없음"
    # 배포물 = 커밋된 내용. 미커밋 변경이 섞이면 prod와 git이 어긋나 다음 CAS가 깨진다.
    git diff --quiet HEAD -- "$f" || fail "$f — 커밋 안 된 변경 있음. 커밋 후 배포."

    # 원격 내용을 **한 번만** 가져와 git blob id로 변환 — 이후 비교는 전부 트리 조회
    # (git rev-parse commit:path)라 내용을 다시 읽지 않는다. 파일 없으면 빈 결과.
    # ★sha256 대신 blob id를 쓰는 이유: 이 저장소가 iCloud Drive에 있어 콜드 I/O가 느리다
    #   (실측: rev-list 1회 50초, CPU 0% = 전부 디스크 대기). 버전마다 `git show | sha256`을
    #   돌리면 파일당 수 분 — 안 쓰게 되는 게이트는 없는 것과 같다. blob 비교는 warm 후 수 초.
    RTMP=$(mktemp)
    ssh -o BatchMode=yes "$HOST" "cat '$REMOTE_REPO/$f' 2>/dev/null" > "$RTMP" || true
    LOCAL_BLOB=$(git rev-parse "HEAD:$f")
    if [ ! -s "$RTMP" ]; then
      echo "  🆕 신규: $f"
      TO_SEND+=("$f"); rm -f "$RTMP"; continue
    fi
    REMOTE_BLOB=$(git hash-object "$RTMP"); rm -f "$RTMP"

    if [ "$REMOTE_BLOB" = "$LOCAL_BLOB" ]; then
      echo "  ⏭  동일(이미 배포됨): $f"; continue
    fi

    # ★핵심 CAS: prod의 현재 내용이 내 브랜치 역사에 있는 버전인가.
    KNOWN=0
    while read -r c; do
      if [ "$(git rev-parse -q --verify "$c:$f" 2>/dev/null)" = "$REMOTE_BLOB" ]; then
        KNOWN=1; break
      fi
    done < <(git rev-list HEAD -- "$f")

    if [ "$KNOWN" = 1 ]; then
      echo "  ✅ CAS 통과(prod=내 역사 속 구버전): $f"
      TO_SEND+=("$f")
    else
      echo "" >&2
      echo "  ★ prod의 $f 내용(blob ${REMOTE_BLOB:0:12}…)이 이 브랜치 역사에 없습니다." >&2
      echo "    = 다른 세션이 배포한 코드입니다. 지금 덮으면 그 기능이 죽습니다" >&2
      echo "      (2026-07-17 qi 수집 clobber 사고와 동일 패턴)." >&2
      echo "    → git fetch 후 그쪽 브랜치/main을 병합하고 다시 실행하세요." >&2
      fail "$f — CAS 실패(미지의 prod 내용)"
    fi
  done

  if [ ${#TO_SEND[@]} -eq 0 ]; then
    echo "  ($kind: 보낼 파일 없음 — 전부 최신)"
    return 0
  fi

  for f in "${TO_SEND[@]}"; do
    scp -q "$f" "$HOST:$REMOTE_REPO/$f"
    L=$(git show "HEAD:$f" | shasum -a 256 | cut -d' ' -f1)
    R=$(ssh -o BatchMode=yes "$HOST" "sha256sum '$REMOTE_REPO/$f' | cut -d' ' -f1")
    [ "$L" = "$R" ] || fail "$f — 전송 후 sha 불일치"
    echo "  📦 배포: $f"
  done
  FILES_JSON=$(printf '"%s",' "${TO_SEND[@]}"); FILES_JSON="[${FILES_JSON%,}]"
  ssh -o BatchMode=yes "$HOST" "printf '%s\n' '{\"ts\":\"$(date -u +%FT%TZ)\",\"kind\":\"$kind\",\"branch\":\"$BRANCH\",\"commit\":\"$COMMIT\",\"files\":$FILES_JSON}' >> '$MANIFEST'"
}

# prod alembic 조회. INFO 로그는 stderr로 나가므로 stdout만 취해 revision id만 추출.
# 출력 예: "a1c3e5f7b9d1 (head) (mergepoint)" → "a1c3e5f7b9d1"
prod_alembic_revs() {   # $1 = current | heads
  ssh -o BatchMode=yes "$HOST" "cd '$REMOTE_REPO/backend' && $REMOTE_ALEMBIC $1 2>/dev/null" \
    | awk '{print $1}' | grep -E '^[0-9a-zA-Z_]+$' | sort | paste -sd, -
}

# ── 1. 배포 목록 분리: alembic 자산은 코드보다 먼저 나가야 한다 ─────────
declare -a MIG_FILES=() CODE_FILES=()
for f in "${FILES[@]}"; do
  case "$f" in
    backend/alembic/*) MIG_FILES+=("$f") ;;
    *)                 CODE_FILES+=("$f") ;;
  esac
done

# ── 2. 마이그레이션 순서 가드 ────────────────────────────────────────
#  (2-a) 커밋된 로컬 마이그레이션 중 prod에 파일 자체가 없는 것 = 반드시 이번에 같이 가야 함
LOCAL_MIGS=$(git ls-files "$MIG_DIR" | grep '\.py$' | sed 's#.*/##' | sort)
REMOTE_MIGS=$(ssh -o BatchMode=yes "$HOST" "ls '$REMOTE_REPO/$MIG_DIR'/*.py 2>/dev/null" | sed 's#.*/##' | sort)
MISSING=$(comm -23 <(printf '%s\n' "$LOCAL_MIGS") <(printf '%s\n' "$REMOTE_MIGS") | sed '/^$/d')

if [ -n "$MISSING" ]; then
  NOT_LISTED=""
  while read -r m; do
    [ -n "$m" ] || continue
    LISTED=0
    if [ ${#MIG_FILES[@]} -gt 0 ]; then
      for f in "${MIG_FILES[@]}"; do
        if [ "$(basename "$f")" = "$m" ]; then LISTED=1; break; fi
      done
    fi
    [ "$LISTED" = 1 ] || NOT_LISTED="$NOT_LISTED  $MIG_DIR/$m"$'\n'
  done <<< "$MISSING"

  if [ -n "$NOT_LISTED" ]; then
    echo "" >&2
    echo "  ★ prod에 아직 없는 마이그레이션 파일이 배포 목록에서 빠졌습니다:" >&2
    printf '%s' "$NOT_LISTED" >&2
    echo "    이 상태로 코드만 배포하면 ORM이 DB에 없는 컬럼을 SELECT해" >&2
    echo "    해당 테이블의 ingest 경로가 통째로 죽습니다(no such column)." >&2
    echo "    → 위 파일들을 배포 목록 앞에 넣고 --migrate 와 함께 다시 실행하세요." >&2
    fail "마이그레이션 파일 누락 — 코드 우선 배포 차단"
  fi
fi

#  (2-b) alembic 자산 먼저 배포(파일 자체는 실행 전까지 무해 — 이 순서가 전제조건)
if [ ${#MIG_FILES[@]} -gt 0 ]; then
  echo "── alembic 자산 선배포 ──"
  deploy_batch "backend-migration" "${MIG_FILES[@]}"
fi

#  (2-c) prod DB가 head에 있는가
PROD_CUR=$(prod_alembic_revs current || true)
PROD_HEADS=$(prod_alembic_revs heads || true)
[ -n "$PROD_HEADS" ] || fail "prod alembic 상태를 읽지 못했습니다($REMOTE_ALEMBIC heads 빈 응답). 원격 venv/경로 확인 후 재시도."

if [ "$PROD_CUR" != "$PROD_HEADS" ]; then
  echo "  ⏳ prod DB 마이그레이션 대기: applied=[${PROD_CUR:-없음}] head=[$PROD_HEADS]"
  if [ "$MIGRATE" = 1 ]; then
    echo "  ▶ 원격 alembic upgrade head 실행…"
    ssh -o BatchMode=yes "$HOST" "cd '$REMOTE_REPO/backend' && $REMOTE_ALEMBIC upgrade head" \
      || fail "원격 alembic upgrade head 실패 — 코드 배포 중단(prod 코드는 아직 구버전이라 안전)."
    AFTER=$(prod_alembic_revs current || true)
    [ "$AFTER" = "$PROD_HEADS" ] || fail "upgrade 후에도 head 불일치(applied=[${AFTER:-없음}] head=[$PROD_HEADS]) — 코드 배포 중단."
    echo "  ✅ 마이그레이션 완료: ${PROD_CUR:-없음} → $AFTER"
    ssh -o BatchMode=yes "$HOST" "printf '%s\n' '{\"ts\":\"$(date -u +%FT%TZ)\",\"kind\":\"alembic\",\"branch\":\"$BRANCH\",\"commit\":\"$COMMIT\",\"from\":\"${PROD_CUR:-}\",\"to\":\"$AFTER\"}' >> '$MANIFEST'"
  else
    echo "" >&2
    echo "  ★ prod DB가 마이그레이션 대기 상태입니다(applied=[${PROD_CUR:-없음}] head=[$PROD_HEADS])." >&2
    echo "    이 앱은 부팅 시 인프로세스 마이그레이션을 하지 않습니다. 지금 코드를 배포/재시작하면" >&2
    echo "    SQLAlchemy가 DB에 없는 컬럼을 SELECT해 **그 경로 전체**가 죽습니다" >&2
    echo "    (2026-07-28 rocket-1p 실증: OperationalError no such column …)." >&2
    echo "    → --migrate 를 붙여 다시 실행하세요(코드 배포 전에 upgrade 수행)." >&2
    echo "      또는 수동으로:" >&2
    echo "      ssh $HOST \"cd $REMOTE_REPO/backend && $REMOTE_ALEMBIC upgrade head\"" >&2
    fail "마이그레이션 미적용 — 코드 배포/재시작 차단"
  fi
else
  echo "  ✅ prod DB = head($PROD_HEADS)"
fi

# ── 3. 코드 파일 배포 ───────────────────────────────────────────────
if [ ${#CODE_FILES[@]} -gt 0 ]; then
  deploy_batch "backend" "${CODE_FILES[@]}"
fi

# ── 4. 재시작(선택) ─────────────────────────────────────────────────
# ★2026-08-05부터 재시작은 **무중단(블루-그린)**이 기본이다. 구 방식(pm2 restart)은
# 콜드부팅 ~50초 동안 전 요청이 502였고, 그 창에서 갱신 버튼의 POST와 Mac 페처의 push가
# 유실됐다(3일간 재시작 61회 = 502 구멍 61개). 상세는 scripts/zero_downtime_restart.sh 헤더.
# --restart-legacy 는 무중단 경로가 고장났을 때의 탈출구다(다운타임 발생을 감수).
if [ "$RESTART" = 1 ]; then
  echo "▶ 무중단 재시작(블루-그린)…"
  if ! "$(dirname "$0")/zero_downtime_restart.sh"; then
    echo "" >&2
    echo "  ★무중단 재시작이 실패했습니다. **코드 파일은 이미 prod에 배포됐고**," >&2
    echo "    구 프로세스가 구버전 코드로 계속 서빙 중일 수 있습니다(사용자 영향은 없음)." >&2
    echo "    위 로그에서 실패 단계를 확인한 뒤 재실행하거나, 부득이하면:" >&2
    echo "      scripts/safe_deploy.sh --restart-legacy   # 다운타임 ~50초 감수" >&2
    fail "무중단 재시작 실패 — 배포는 완료, 재시작만 미완"
  fi
elif [ "$RESTART_LEGACY" = 1 ]; then
  # ★2026-08-10 수리: 이 탈출구는 «성공이라 말하면서 아무것도 안 하고» 있었다.
  #   블루-그린(2026-08-05)이 pm2 앱 이름을 ohisell-backend-8001/8011로 바꿨는데 여기는
  #   여전히 `pm2 restart ohisell-backend`를 불렀다. 이름이 없으니 pm2가 실패하는데
  #   `2>&1 >/dev/null`이 그걸 삼키고, 스크립트는 그대로 "✅ 배포 완료"를 찍었다.
  #   실측(2026-08-10 16:04): pid·uptime 불변인데 성공 출력 — 무중단 경로가 막힌 상황에서
  #   유일한 탈출구가 거짓 초록이면 구버전이 계속 돌면서 배포됐다고 믿게 된다.
  #   그래서 ①실제 앱 이름을 찾아 재시작하고 ②pid가 바뀌었는지·헬스가 도는지 검증하고
  #   ③아니면 실패로 끝낸다.
  echo "⚠️  레거시 재시작(다운타임 ~50초 — 그 사이 요청은 502가 됩니다)"
  ssh -o BatchMode=yes "$HOST" bash -s <<'REMOTE' || fail "레거시 재시작 실패 — 배포는 완료, 재시작만 미완"
set -u
APP=$(pm2 jlist 2>/dev/null | python3 -c '
import json,sys
try: procs=json.load(sys.stdin)
except Exception: sys.exit(1)
for p in procs:
    n=p.get("name","")
    if n.startswith("ohisell-backend") and p.get("pm2_env",{}).get("status")=="online":
        print(n); break
')
[ -n "${APP:-}" ] || { echo "  ❌ 온라인 상태인 ohisell-backend* 프로세스를 못 찾았습니다." >&2; exit 1; }
PORT=$(printf '%s' "$APP" | grep -oE '[0-9]+$' || true); PORT=${PORT:-8001}
OLD=$(pm2 jlist | python3 -c "import json,sys;print(next((p['pid'] for p in json.load(sys.stdin) if p['name']=='$APP'),''))")
echo "  대상: $APP (:$PORT) · 현재 pid=$OLD"
pm2 restart "$APP" >/dev/null || { echo "  ❌ pm2 restart $APP 실패" >&2; exit 1; }
for i in $(seq 1 40); do
  sleep 3
  NEW=$(pm2 jlist | python3 -c "import json,sys;print(next((p['pid'] for p in json.load(sys.stdin) if p['name']=='$APP'),''))")
  if [ -n "$NEW" ] && [ "$NEW" != "$OLD" ] && curl -sf -m 5 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "  ✅ 재시작 확인: pid $OLD → $NEW · 헬스 OK(:$PORT)"; exit 0
  fi
done
echo "  ❌ 재시작 검증 실패(pid 불변이거나 헬스 무응답) — 구버전이 계속 돌고 있을 수 있습니다." >&2
exit 1
REMOTE
fi
echo "✅ 배포 완료 (commit $COMMIT)"
