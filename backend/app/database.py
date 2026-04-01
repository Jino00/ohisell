# database.py — SQLAlchemy 엔진 및 세션 설정
import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ohisell.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── ohi-ad-intelligence ad_data.db (읽기 전용) ──
_ad_engine = None
_AdSessionLocal = None


def _init_ad_engine():
    global _ad_engine, _AdSessionLocal
    ad_path = os.getenv("AD_DATA_DB_PATH", "")
    if not ad_path or not os.path.isfile(ad_path):
        log.warning("AD_DATA_DB_PATH 미설정이거나 파일 없음: %s", ad_path)
        return

    uri = f"sqlite:///file:{ad_path}?mode=ro&uri=true"
    try:
        _ad_engine = create_engine(uri, connect_args={"check_same_thread": False})
        with _ad_engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM daily_ad_data LIMIT 1"))
        _AdSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_ad_engine)
        log.info("ad_data.db 연결 성공 (읽기 전용): %s", ad_path)
    except Exception as e:
        log.error("ad_data.db 연결 실패: %s", e)
        _ad_engine = None
        _AdSessionLocal = None


def get_ad_db():
    if _AdSessionLocal is None:
        _init_ad_engine()
    if _AdSessionLocal is None:
        return None
    db = _AdSessionLocal()
    try:
        yield db
    finally:
        db.close()
