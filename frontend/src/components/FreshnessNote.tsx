// FreshnessNote.tsx — 로켓1P 화면의 상설 신선도 표면 (2026-08-06 적대 리뷰 P1).
//
// 왜 있나: 쿠팡 판매분석은 **당일·전일치를 주지 않는다.** 그래서 "최근 7일"을 열면 늘 5일치만
//   들어오는데, 화면이 그걸 7일이라 말하면 주간 비교에서 이번 주가 **항상 20% 낮게** 나온다.
//   더 나쁜 건 수집이 진짜 멈췄을 때도 화면상 구별할 수단이 없다는 것이다.
//   (이 프로젝트가 반복해 당한 형태 — 상설 배너가 없으면 방치된다.)
//
// ★두 상태를 색으로 가른다. 늘 경고가 떠 있으면 아무도 안 본다 — 경보가 거짓말하면 경보가 죽는다.
//     정상 지연(D-1~D-2)  → 회색 사실 진술
//     stale(임계 초과)     → 노랑 경고
import type { WindowFreshness } from "../lib/api";

export function FreshnessNote({ f }: { f: WindowFreshness }) {
  if (f.data_as_of == null) {
    return (
      <p className="mx-4 mb-3 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
        ⚠️ 이 기간에 판매분석 데이터가 <b>하나도 없습니다</b>. 쿠팡 판매분석은 롤링 약 2개월만
        조회되므로, 오래된 기간이면 정상입니다 — 이 화면의 판매 축 값은 0이 아니라 <b>모름</b>입니다.
      </p>
    );
  }
  const tone = f.stale
    ? "bg-amber-50 text-amber-800"
    : "bg-gray-50 text-gray-600";
  return (
    <p className={`mx-4 mb-3 rounded px-3 py-2 text-xs ${tone}`}>
      {f.stale ? "⚠️ " : ""}
      데이터 기준 <b>{f.data_as_of}</b>
      {f.lag_days != null && f.lag_days > 0 && <> · 요청 종료일보다 <b>{f.lag_days}일</b> 이전</>}
      {" · "}요청 {f.days_expected}일 중 <b>{f.days_with_data}일</b>에 데이터
      {f.days_no_data > 0 && <> (무데이터 {f.days_no_data}일)</>}
      {f.stale
        ? " — 원천 정상 지연을 넘었습니다. 수집이 멈췄는지 확인하세요."
        : " — 판매분석은 당일·전일치를 주지 않아 최근 1~2일이 비는 것은 정상입니다."}
    </p>
  );
}
