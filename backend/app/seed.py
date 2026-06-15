# seed.py — 초기 시드 데이터 (7개 판매 채널 계정)
from app.database import SessionLocal
from app.models import Channel

CHANNELS = [
    {
        "name": "쿠팡 Wing 계정1",
        "code": "COUPANG_WING1",
        "platform": "coupang",
        "channel_type": "marketplace",
        "account_label": "Wing 3P #1",
        "company": "개인회사 오픽스",
        "commission_rate": 7.8,  # 3P 실측 판매수수료율(폴백). 실측은 coupang_revenue_fee 우선(D-E)
        "api_type": "hmac",
        "api_config_key": "COUPANG_WING1",
    },
    {
        "name": "쿠팡 Wing 계정2",
        "code": "COUPANG_WING2",
        "platform": "coupang",
        "channel_type": "marketplace",
        "account_label": "Wing 3P #2",
        "company": "주식회사 오하이테크",
        "commission_rate": 7.8,  # 3P 실측 판매수수료율(폴백). 실측은 coupang_revenue_fee 우선(D-E)
        "api_type": "hmac",
        "api_config_key": "COUPANG_WING2",
    },
    {
        "name": "쿠팡 로켓그로스 계정1",
        "code": "COUPANG_RG1",
        "platform": "coupang",
        "channel_type": "marketplace",
        "account_label": "로켓그로스 2P #1",
        "company": "개인회사 오픽스",
        "commission_rate": 10.8,
        "api_type": "hmac",
        "api_config_key": "COUPANG_RG1",
    },
    {
        "name": "쿠팡 로켓그로스 계정2",
        "code": "COUPANG_RG2",
        "platform": "coupang",
        "channel_type": "marketplace",
        "account_label": "로켓그로스 2P #2",
        "company": "주식회사 오하이테크",
        "commission_rate": 10.8,
        "api_type": "hmac",
        "api_config_key": "COUPANG_RG2",
    },
    {
        "name": "쿠팡 로켓배송",
        "code": "COUPANG_ROCKET",
        "platform": "coupang",
        "channel_type": "consignment",
        "account_label": "로켓배송 1P",
        "company": "주식회사 오하이테크",
        "commission_rate": 0,
        "api_type": "excel",
        "api_config_key": None,
        "memo": "위탁판매 — 광고리포트 엑셀 or ohi-ad-intelligence DB 연동",
    },
    {
        "name": "네이버 스마트스토어",
        "code": "NAVER",
        "platform": "naver",
        "channel_type": "marketplace",
        "account_label": None,
        "company": "주식회사 오하이",
        "commission_rate": 5.5,
        "api_type": "oauth2_bcrypt",
        "api_config_key": "NAVER",
    },
    {
        "name": "자사몰 (cafe24)",
        "code": "CAFE24",
        "platform": "cafe24",
        "channel_type": "own",
        "account_label": None,
        "company": "주식회사 오하이테크",
        "commission_rate": 0,
        "api_type": "oauth2_code",
        "api_config_key": "CAFE24",
    },
]


def seed_channels():
    db = SessionLocal()
    try:
        for ch in CHANNELS:
            exists = db.query(Channel).filter_by(code=ch["code"]).first()
            if not exists:
                db.add(Channel(**ch))
            elif ch.get("company") and exists.company != ch["company"]:
                # 기존행 company 누락/변경 시 backfill (fresh bootstrap에서
                # 마이그레이션 UPDATE가 빈 테이블에 no-op 된 경우 복구)
                exists.company = ch["company"]
        db.commit()
        count = db.query(Channel).count()
        print(f"Channels: {count}개 등록됨")
    finally:
        db.close()


if __name__ == "__main__":
    seed_channels()
