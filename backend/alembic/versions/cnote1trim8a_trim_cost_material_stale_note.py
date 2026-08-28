"""cost_material.note에서 「— 단가는 아직 없다」 상태 주장을 떼어낸다 (백필)

Jino 승인 2026-08-28 11:27 KST (원문 *"그래"*) — 계약 `docs/contracts/CONTRACT_cost_excel_roundtrip.md`
D-CPP-62 「원가 왕복 목표」 S2, 체인 `sellc-원가-메뉴` n=16.

왜 고치나 — **화면이 74행에서 거짓말을 하고 있었다.**
  `recipes.py`가 새 종을 만들 때 `note`에 「…으로 생성 — 단가는 아직 없다」를 박는다. 만드는
  순간엔 참이다. 그런데 **나중에 단가가 들어와도 아무도 그 문장을 갱신하지 않는다.** 배포 전
  prod 실측(2026-08-28 11:1x, 읽기 전용):

      전체 종                     139
      그 문구를 단 종               138
      ★그 문구 + 단가 실재(=거짓)      74
      그 문구 + 단가 없음(=참)        64        ← 인박스 「단가 없음 64건」과 정확히 일치

  즉 **살아 있는 판정은 이미 화면 배지(`▢단가없음`)가 `latest_price_*`를 보고 라이브로 하고
  있고**, `note`는 그 옆에서 굳은 값으로 우기고 있었다. 「상태」는 자유 텍스트가 아니라 계산이
  답해야 하는 것이다 — 이 마이그레이션은 그 «중복된 두 번째 답»을 지운다.

  ★그리고 이 열은 **S3 다운로드 파일의 한 열**이다(설계 Q3). 안 고치면 74행이 거짓말을 실은
  채 엑셀로 나가고, 그걸 받아 본 사람이 파일을 믿는다.

무엇을 남기고 무엇을 떼나
  · 남긴다 — 「원가 정본 엑셀에서 구성 파싱으로 생성」 / 「원가표 항목 픽(D-CPP-59)으로 생성」.
    **출처**라서 시간이 지나도 참이다.
  · 뗀다 — 「 — 단가는 아직 없다」. **현재 상태 주장**이라 시간이 지나면 틀린다.

왜 exact match가 아니라 substring replace인가
  실측 시점(11:1x)엔 변종이 정확히 두 개뿐이었다(엑셀 135 · 픽 3 · 사람이 쓴 것 1, **줄바꿈
  0건**). 그러나 `recipes.py:946`은 카테고리 변경 시 `note`에 스탬프를 **append**한다 —
  이 마이그레이션이 prod에서 도는 사이 다른 세션이 픽을 하면 「정형문\\n스탬프」가 생기고
  exact match는 그 행을 조용히 건너뛴다. 그래서 **접미사 substring만** 지운다.
  그 문구를 쓰는 자리는 위 두 곳뿐이므로(`grep` 전수) 사람이 쓴 비고를 건드릴 경로가 없다.

되돌리기 — **완전한 역함수가 아니다. 알고 쓴다.**
  downgrade는 두 출처 문구로 **끝나는** 행에 접미사를 다시 붙인다. 한계가 둘이다:
    ① 원래부터 접미사가 없던 행이 있었다면 그 행에도 붙는다 — 실측상 0건이다(정형문을 단
       138행이 전부 접미사를 갖고 있었다).
    ② **스탬프가 append된 행(변종 C)은 복원되지 않는다.** 그 행의 note는 출처 문구로 «끝나지»
       않아 `LIKE '%<출처>'`에 안 걸린다. 접미사는 첫 줄 중간에 있었기 때문이다.
  ⇒ 이 마이그레이션의 정정 경로는 downgrade가 아니라 **「note는 출처만 적는다」는 규칙 자체의
     재검토**다. 지워지는 것이 «틀린 문장»이라 되살릴 가치가 애초에 낮다.

검증 (2026-08-28 11:4x, prod 실측 변종 5종을 옮긴 in-memory 표본에 실제로 실행)
  · A 엑셀 정형문 135건 모양 → 접미사만 떨어짐 ✅
  · B 픽 정형문 3건 모양 → 접미사만 떨어짐 ✅
  · D 사람이 쓴 비고 1건(cleaning kit) → **무변화** ✅
  · note NULL → **무변화** ✅
  · C 정형문 + append 스탬프(prod 0건, 배포 중 생길 수 있는 모양) → 접미사만 떨어지고
    **스탬프와 줄바꿈 구조는 살아남음** ✅

배포 순서: `safe_deploy.sh <이 파일> <코드> --migrate --restart`가 ①마이그→②upgrade→③코드
  →④재시작을 강제한다. 컬럼을 지우지 않는 **데이터 전용** 변경이라 구코드를 깨지 않는다.

Revision ID: cnote1trim8a
Revises: ricfm1w7b
Create Date: 2026-08-28 (KST)
"""
from alembic import op

revision = "cnote1trim8a"
down_revision = "ricfm1w7b"
branch_labels = None
depends_on = None

SUFFIX = " — 단가는 아직 없다"
ORIGINS = (
    "원가 정본 엑셀에서 구성 파싱으로 생성",
    "원가표 항목 픽(D-CPP-59)으로 생성",
)


def upgrade() -> None:
    op.execute(
        """
        UPDATE cost_material
           SET note = replace(note, ' — 단가는 아직 없다', '')
         WHERE note LIKE '%— 단가는 아직 없다%'
        """
    )


def downgrade() -> None:
    for origin in ORIGINS:
        op.execute(
            f"""
            UPDATE cost_material
               SET note = note || '{SUFFIX}'
             WHERE note LIKE '%{origin}'
            """
        )
