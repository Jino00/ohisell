# 인계 실측 상세 (2026-08-17, HANDOFF_rank-axis+paper-application+exclusion-slots)

전체 명령·전체 출력 원본은 이 파일에 보존. 본문(에이전트 최종 응답)은 압축본.

## 실행한 전체 명령 목록
- `python3 -m pytest tests/test_hierarchical_pooling.py -q` (backend/) → 18 passed
- grep 다수 (pool_all/pooled_rpc/ngram/bidWeight, backend/app·backend/tests, __pycache__ 제외)
- SQL: claim3_shopping_proposals.sql, claim3_followup.sql (via ssh sqlite3 -readonly)
- `ssh sellc.ohitech.co.kr grep '검색어 제외:' *.log` (prod 로그, 읽기 전용)

## 원본 SQL 결과 요약(핵심)
- naver_proposals(search_term_exclude) all-time: WEB_SITE/approved 1건뿐(2026-07-22), SHOPPING 0건.
- naver_campaign_settings: 총 7행, 전부 optimizer='none'. BRAND_SEARCH 2개 캠페인 0행.
- ops_diary_entries: action='search_term_exclude' 전체 2건뿐(둘 다 console actor, execute/voided) — 쇼핑 브리핑(observe) 문구 매치 0건, 전체 기간(2026-07-16~08-17).
- prod 로그 grep '검색어 제외:' — 2026-07-22~08-16 표본 전부 shopping=0.

(SQL 파일 2개, 이 디렉터리에 있음: claim3_shopping_proposals.sql, claim3_followup.sql)
