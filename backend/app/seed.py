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
        "sell_type": "3P",
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
        "sell_type": "3P",
    },
    {
        "name": "쿠팡 로켓그로스 계정1",
        "code": "COUPANG_RG1",
        "platform": "coupang",
        "channel_type": "marketplace",
        "account_label": "로켓그로스 2P #1",
        "company": "개인회사 오픽스",
        # RG 판매수수료도 3P와 동일 7.8%(Jino 확정 2026-07-27). 정산 실측 대조:
        # WING1 2026-06-15~21 sale_fee(VAT포함) 303,449 / RG매출 3,534,160 = 8.586% = 7.8%×1.1 정확 일치.
        # 누적(06-01~07-19) 3,056,638/38,072,570 = 8.03% → ÷1.1 = 명목 7.3%(분모=paid_at 기준이라 인식일과 어긋난 오차).
        # 10.8%였다면 VAT포함 11.88%가 나와야 하나 어느 주도 8.6%를 넘지 않음.
        "commission_rate": 7.8,
        "api_type": "hmac",
        "api_config_key": "COUPANG_RG1",
        "sell_type": "RG",
    },
    {
        "name": "쿠팡 로켓그로스 계정2",
        "code": "COUPANG_RG2",
        "platform": "coupang",
        "channel_type": "marketplace",
        "account_label": "로켓그로스 2P #2",
        "company": "주식회사 오하이테크",
        "commission_rate": 7.8,  # RG1과 동일(Jino 확정 2026-07-27) — RG2는 정산 표본 부족으로 RG1 실측 준용
        "api_type": "hmac",
        "api_config_key": "COUPANG_RG2",
        "sell_type": "RG",
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
        "sell_type": "1P",
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
            else:
                if ch.get("company") and exists.company != ch["company"]:
                    # 기존행 company 누락/변경 시 backfill (fresh bootstrap에서
                    # 마이그레이션 UPDATE가 빈 테이블에 no-op 된 경우 복구)
                    exists.company = ch["company"]
                if ch.get("sell_type") and exists.sell_type != ch["sell_type"]:
                    exists.sell_type = ch["sell_type"]
        db.commit()
        count = db.query(Channel).count()
        print(f"Channels: {count}개 등록됨")
    finally:
        db.close()


if __name__ == "__main__":
    seed_channels()
