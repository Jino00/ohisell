# seed.py — 초기 시드 데이터 (판매 채널)
from app.database import SessionLocal
from app.models import Channel

CHANNELS = [
    {"name": "자사몰", "code": "OWN", "commission_rate": 0},
    {"name": "쿠팡", "code": "COUPANG", "commission_rate": 10.8},
    {"name": "네이버 스마트스토어", "code": "NAVER", "commission_rate": 5.5},
]


def seed_channels():
    db = SessionLocal()
    try:
        for ch in CHANNELS:
            exists = db.query(Channel).filter_by(code=ch["code"]).first()
            if not exists:
                db.add(Channel(**ch))
        db.commit()
        print(f"Seeded {len(CHANNELS)} channels")
    finally:
        db.close()


if __name__ == "__main__":
    seed_channels()
