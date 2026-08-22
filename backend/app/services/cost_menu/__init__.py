"""원가 메뉴·표준원가 (계약 A′ / D-CPP-53).

`matcher.py`는 DB도 IO도 모르는 **순수 SA**이고, `materials.py`가 그것을 부르는 얇은 DB 층이다
(계약 B의 `allocator.py` ↔ `ledger.py`와 같은 형태). 사본 두 벌은 «감시자가 감시 대상보다
낡는» 형태다(계약 §2-6).
"""
