# LESSONS_LEARNED.md — ohisell 프로젝트 학습 기록

## 1. Python 3.14 + SQLAlchemy 호환성 이슈

### 이슈
SQLAlchemy 2.0.40에서 `Mapped[str | None]` 사용 시 `TypeError: descriptor '__getitem__' requires a 'typing.Union' object` 에러 발생. Python 3.14의 typing 내부 변경으로 `Union.__getitem__` 동작이 달라짐.

### 해결
1. SQLAlchemy 2.0.48로 업그레이드
2. `from __future__ import annotations` 추가
3. `str | None` 대신 `Optional[str]` 사용

### 교훈
Python 3.14는 아직 최신이라 라이브러리 호환성 이슈가 있을 수 있음. SQLAlchemy는 반드시 2.0.48 이상 사용할 것. 새 Python 버전 사용 시 첫 마이그레이션에서 호환성을 바로 검증해야 함.
