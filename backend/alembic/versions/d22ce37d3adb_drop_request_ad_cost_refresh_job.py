"""drop request_ad_cost_refresh job

Revision ID: d22ce37d3adb
Revises: u3v4w5x6y7z8
Create Date: 2026-07-20 11:13:42.763818

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd22ce37d3adb'
down_revision: Union[str, None] = 'u3v4w5x6y7z8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """prod SchedulerState에 이미 시드된 request_ad_cost_refresh 행 제거(순수 on-demand 전환).

    잡 등록은 SchedulerState 행에서 이뤄지므로 defaults 리스트 제거만으론 prod에서 안 사라진다.
    이 행이 남아 있으면 start_scheduler가 (이제 job_func_for=None인) 잡을 매번 skip하며 로그만
    남기고, 무엇보다 크론이 살아있는 것처럼 보인다. 명시적으로 삭제한다."""
    op.execute("DELETE FROM scheduler_state WHERE job_name = 'request_ad_cost_refresh'")


def downgrade() -> None:
    """롤백: 크론 행 복원(장중 자동 갱신 재활성). 코드 쪽 잡 함수/맵도 함께 되돌려야 동작한다."""
    op.execute(
        "INSERT INTO scheduler_state (job_name, cron_expression, is_enabled) "
        "VALUES ('request_ad_cost_refresh', '0 3,10-20 * * *', 1)"
    )
