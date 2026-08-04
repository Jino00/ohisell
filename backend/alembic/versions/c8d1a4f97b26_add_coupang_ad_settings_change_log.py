"""쿠팡 광고 설정 스냅샷·변경 이력 (트랙 coupang-ad-change-log, D-CAC-3/4)

네이버 「수정 사항」이 서는 다리는 둘 — ①우리 실집행 로그 ②설정 스냅샷 diff. **쿠팡은 둘 다 없었다**:
우리가 쿠팡 광고를 쓰는 경로가 아예 없고(services/coupang에 광고 쓰기 0건), 광고 테이블 3종은
성과(비용·노출·클릭·전환)만 담아 diff를 뜰 설정 상태 자체가 없었다. 이 두 테이블이 그 공백을 메운다.

★쿠팡은 **모든 변경이 외부다** — 네이버와 결정적으로 다른 점이라 '주체' 컬럼을 두지 않는다.
  isAgencyManaged는 '현재 관리 주체'지 '이번 변경을 누가 했나'가 아니다(근거 없는 단정 금지).

★두 축 분리(D-CAC-3, Jino 원문 "On되어 있는 캠페인만 보면 되는거야"):
  존재·On/Off는 전량에서, 내용 diff는 활성만. 오하이테크 캠페인 525개 중 활성 16개라
  전량을 필드 단위로 매번 비교할 이유가 없다(서버가 isActive:true 필터를 받아준다 — 1콜).

★성과 필드는 스냅샷에 넣지 않는다. spentBudget이 20분에 6,501→6,592로 움직인 걸 실측했다
  (2026-08-04 오픽스). 넣으면 매일 아침 "전 캠페인 수정됨"이 뜬다 — 네이버에서 소재 editTm이
  상품 피드 재적용으로 전진해 ad_edit이 229:4로 오염된 것과 같은 함정이다.

가산적이다: 기존 테이블·집계 로직은 한 줄도 안 바뀐다(계약 금지선).

Revision ID: c8d1a4f97b26
Revises: b3e75c1a8f04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8d1a4f97b26"
down_revision: Union[str, Sequence[str], None] = "b3e75c1a8f04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coupang_ad_entity_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account", sa.String(length=12), nullable=False),      # ofix / ohitech
        sa.Column("entity_type", sa.String(length=12), nullable=False),  # campaign / adgroup
        sa.Column("entity_id", sa.String(length=24), nullable=False),
        sa.Column("campaign_id", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        # 설정 필드만(성과 필드 제외). 활성 조회에서만 갱신 — 전량 조회는 id·name·isActive만 준다.
        sa.Column("settings_json", sa.Text(), nullable=True),
        # 쿠팡 updatedAt(UTC naive) — 발생 시각 앵커. 네이버 소재 editTm과 같은 역할.
        sa.Column("src_updated_at", sa.DateTime(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        # 마지막 전량 조회에 있었나. 삭제(목록에서 소멸) vs Off(목록에 남음) 구분자.
        sa.Column("present", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account", "entity_type", "entity_id",
                            name="uq_coupang_ad_entity_snapshot"),
    )
    op.create_index("ix_coupang_ad_entity_snapshot_account", "coupang_ad_entity_snapshot",
                    ["account"])
    op.create_index("ix_coupang_ad_entity_snapshot_campaign_id", "coupang_ad_entity_snapshot",
                    ["campaign_id"])

    op.create_table(
        "coupang_ad_change_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account", sa.String(length=12), nullable=False),
        sa.Column("entity_type", sa.String(length=12), nullable=False),
        sa.Column("entity_id", sa.String(length=24), nullable=False),
        sa.Column("campaign_id", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("entity_name", sa.String(length=200), nullable=False, server_default=""),
        # created / turned_on / turned_off / deleted / field_change
        sa.Column("op", sa.String(length=16), nullable=False),
        # field_change일 때만 채운다. NULL이 아니라 ''인 이유: SQLite에서 NULL은 서로 달라
        # UNIQUE 제약이 중복을 막지 못한다.
        sa.Column("field", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("before_value", sa.Text(), nullable=True),
        sa.Column("after_value", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        # src = 쿠팡 updatedAt(진짜 발생 시각) / detected = 우리가 알아챈 시각.
        # 네이버에서 감지일로 귀속했다가 07-30 변경을 08-03으로 잡은 실사고가 있었다.
        sa.Column("time_basis", sa.String(length=10), nullable=False, server_default="detected"),
        sa.Column("detected_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # 같은 스냅샷을 두 번 넣어도 행이 안 늘게(회차 재실행·백필 idempotent).
        sa.UniqueConstraint("account", "entity_type", "entity_id", "op", "field", "occurred_at",
                            name="uq_coupang_ad_change_log"),
    )
    op.create_index("ix_coupang_ad_change_log_account", "coupang_ad_change_log", ["account"])
    op.create_index("ix_coupang_ad_change_log_campaign_id", "coupang_ad_change_log",
                    ["campaign_id"])
    op.create_index("ix_coupang_ad_change_log_occurred_at", "coupang_ad_change_log",
                    ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_coupang_ad_change_log_occurred_at", table_name="coupang_ad_change_log")
    op.drop_index("ix_coupang_ad_change_log_campaign_id", table_name="coupang_ad_change_log")
    op.drop_index("ix_coupang_ad_change_log_account", table_name="coupang_ad_change_log")
    op.drop_table("coupang_ad_change_log")
    op.drop_index("ix_coupang_ad_entity_snapshot_campaign_id",
                  table_name="coupang_ad_entity_snapshot")
    op.drop_index("ix_coupang_ad_entity_snapshot_account",
                  table_name="coupang_ad_entity_snapshot")
    op.drop_table("coupang_ad_entity_snapshot")
