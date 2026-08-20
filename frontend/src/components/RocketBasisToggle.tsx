import type { GroupedSummaryRow, RocketBasis } from "../lib/api";

/**
 * 로켓배송 1P 매출 축 토글.
 *
 * 왜 필요한가: 1P는 "쿠팡에 납품한 것"과 "고객에게 팔린 것"이 다른 시점의 다른 금액이다.
 *   실측 2026-08-04 하루 — 계산서 1,578,000원 vs 판매(납품가) 3,885,820원. 두 배 넘게 다르다.
 *   어느 쪽도 틀리지 않았고 쓰임이 다르다: 계산서는 회계·세무 정본, 판매는 일일 이익·광고 판단.
 * ★두 축은 **택일**이다 — 더하면 같은 물건을 납품 시점과 판매 시점에 두 번 세는 이중계상이다.
 * ★축 이름을 항상 화면에 띄운다: 라벨이 없으면 둘 중 하나는 반드시 오독된다.
 */
export function RocketBasisToggle({
  value,
  onChange,
  rows,
}: {
  value: RocketBasis;
  onChange: (v: RocketBasis) => void;
  rows: GroupedSummaryRow[];
}) {
  const leaf = rows.find((r) => r.revenue_basis);
  const coverage = typeof leaf?.cost_coverage === "number" ? leaf.cost_coverage : null;
  const burdenUnknown = leaf ? leaf.promo_burden === null : false;
  // 손익을 못 잰 채 광고비만 반영된 금액(D-22). 전체 행이 그 합을 들고 있다.
  const total = rows.find((r) => r.kind === "total");
  const floorAd = Number(total?.net_floor_ad ?? 0);   // 호출부의 사전 파싱에 기대지 않는다

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500">로켓배송 매출 축</span>
      <div className="flex bg-gray-100 rounded-lg p-0.5">
        {([
          ["settlement", "계산서", "세금계산서 지급액 — 회계·세무 정본. 계산서 발행일에 매출이 몰려 뜬다(주 3~4장)"],
          ["sales", "판매(납품가)", "판매분석 판매량 × 납품단가 — 우리 손익. 날짜별 실제 판매 흐름"],
        ] as [RocketBasis, string, string][]).map(([k, label, title]) => (
          <button
            key={k}
            title={title}
            onClick={() => onChange(k)}
            className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
              value === k
                ? "bg-white shadow text-blue-700 font-medium"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {value === "sales" && coverage !== null && (
        <span
          className={`text-xs px-2 py-1 rounded ${
            coverage >= 0.95 ? "bg-green-50 text-green-700" : "bg-amber-50 text-amber-700"
          }`}
          title={
            coverage >= 0.95
              ? "원가가 매출 전반에 붙어 순이익을 낼 수 있다"
              : "원가가 일부에만 붙어 순이익을 내지 않는다 — 부분 표본에 마진을 곱하면 창작이 된다"
          }
        >
          원가 커버리지 {(coverage * 100).toFixed(1)}%
          {coverage < 0.95 && " · 순이익 미산정"}
        </span>
      )}
      {floorAd > 0 && (
        <span
          className="text-xs px-2 py-1 rounded bg-amber-50 text-amber-700"
          title={
            "이 축에서는 로켓배송의 매출·원가가 손익에 안 닿는다. 확정 비용인 광고비만 반영했으므로 " +
            "이 화면의 순이익은 실제 손익의 **하한**이다. 날짜별 실제 손익은 「판매(납품가)」 축으로 본다."
          }
        >
          손익 미측정 광고비 {Math.round(floorAd).toLocaleString("ko-KR")}원 반영(하한)
        </span>
      )}
      {value === "sales" && burdenUnknown && (
        <span
          className="text-xs px-2 py-1 rounded bg-amber-50 text-amber-700"
          title="프로모션 개당 할인액 원천이 이 환경에 없어 분담금을 0이 아니라 '모름'으로 둔다"
        >
          분담금 미상
        </span>
      )}
    </div>
  );
}
