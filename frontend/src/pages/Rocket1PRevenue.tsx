// Rocket1PRevenue.tsx — 로켓배송(1P) 매출 두 축 대조 화면 (조회 전용).
//
// Jino 원문(2026-08-06): "나는 SellC 화면에서 소비자 판매가도 포함된 매출 대시보드를 보고 싶은데."
//
// 왜 이 화면이 필요했나: 쿠팡 판매분석 화면엔 **우리 매출이 없고**, 우리 종합조망엔
//   **소비자 판매가가 없다.** 08-04 하루를 두 화면에서 보면 6,536,000원과 3,885,820원이
//   나오는데 왜 다른지 어디서도 확인할 수 없었다. 이 화면이 두 축을 나란히 놓는다.
//
// ★정직성 규칙 4개 — 숫자를 예쁘게 만들려고 어기지 말 것:
//   1) **두 매출을 더하지 않는다.** 같은 물건이라 더하면 이중계상이다. 열 이름에 축을 박아
//      (「소비자 매출(쿠팡가)」·「우리 매출(납품가)」) 무엇을 보고 있는지 항상 드러낸다.
//   2) **납품단가 미상은 "—"다.** 0으로 그리면 "공짜로 줬다"가 된다. 몇 개가 그런지 커버리지로 센다.
//   3) **RoAS는 우리 매출 기준.** 소비자 매출로 내면 우리가 못 번 돈으로 광고를 정당화하게 된다.
//   4) **순이익을 여기 놓지 않는다.** 이 화면은 매출 축 대조 전용이고 회계는 종합조망이 한다(D-CPP-2).
import { useState } from "react";
import { Card, Stat, Table, Th, Td, Loading, EmptyState, Badge } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { FreshnessNote } from "../components/FreshnessNote";
import { fetchRocket1PRevenue, type Rocket1PRevenueOption } from "../lib/api";

const NO_DATA = "—";

const num = (v: number | null | undefined) => (v == null ? NO_DATA : v.toLocaleString("ko-KR"));

/** 금액은 Decimal → string으로 온다. 숫자 변환은 표시 직전에만(정밀도 보존). */
const won = (v: string | number | null | undefined) => {
  if (v == null) return NO_DATA;
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `${Math.round(n).toLocaleString("ko-KR")}원` : NO_DATA;
};

const pct = (v: string | number | null | undefined, digits = 1) => {
  if (v == null) return NO_DATA;
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : NO_DATA;
};

const ratio = (v: string | null) => {
  if (v == null) return NO_DATA;
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : NO_DATA;
};

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

function daysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return isoKST(d);
}

function OptionRow({ o }: { o: Rocket1PRevenueOption }) {
  return (
    <tr className="hover:bg-gray-50">
      <Td>
        <div className="max-w-md truncate text-gray-900" title={o.product_name ?? o.option_id}>
          {o.product_name ?? o.option_id}
        </div>
        <div className="mt-0.5 text-xs text-gray-400">
          옵션 {o.option_id}{o.sku_id ? ` · SKU ${o.sku_id}` : ""}
        </div>
      </Td>
      <Td right>{num(o.qty)}</Td>
      <Td right>{won(o.consumer_revenue)}</Td>
      {/* 우리 매출만 색을 준다 — 이 화면에서 실제로 우리 돈인 축은 하나뿐이다. */}
      <Td right><span className="font-medium text-blue-700">{won(o.our_revenue)}</span></Td>
      <Td right>{pct(o.our_share)}</Td>
      <Td right>{won(o.unit_price)}</Td>
      <Td right>{num(o.visitors)}</Td>
      <Td right>{won(o.ad_spend)}</Td>
      <Td right>{ratio(o.roas)}</Td>
    </tr>
  );
}

export default function Rocket1PRevenue() {
  const [from, setFrom] = useState(daysAgo(6));
  const [to, setTo] = useState(isoKST(new Date()));

  const { data, error } = useAsyncData(
    () => fetchRocket1PRevenue({ from, to, limit: 300 }),
    [from, to],
  );

  const priced = data?.coverage.priced_pct;
  const coverageTone = priced == null ? "neutral" : Number(priced) >= 0.95 ? "neutral" : "warn";

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-gray-900">로켓배송(1P) 매출 — 소비자가 ∥ 납품가</h1>
        <p className="mt-1 text-sm text-gray-500">
          쿠팡이 고객에게 판 금액과 우리가 쿠팡에 판 금액을 나란히 봅니다. 조회 전용이며,
          이 화면의 값은 종합조망 순이익에 결합되지 않습니다.
        </p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-3 px-4 py-3">
          <label className="text-sm">
            <span className="mr-2 text-gray-500">시작</span>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)}
              className="rounded border border-gray-300 px-2 py-1" />
          </label>
          <label className="text-sm">
            <span className="mr-2 text-gray-500">종료</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)}
              className="rounded border border-gray-300 px-2 py-1" />
          </label>
        </div>
      </Card>

      {error && (
        <Card><EmptyState reason="매출 데이터를 불러오지 못했습니다." hint={String(error)} /></Card>
      )}
      {!data && !error && <Card><Loading /></Card>}

      {data && (
        <>
          {/* ── 두 축을 나란히. 사이에 「우리 몫」을 놓아 왜 다른지 한 눈에 보이게 한다 ── */}
          <Card title="매출 두 축">
            <FreshnessNote f={data.freshness} />
            <div className="grid grid-cols-2 gap-4 px-4 py-4 md:grid-cols-5">
              <Stat label="판매수량" value={num(data.totals.qty)} />   {/* null=미수집 → "—" */}
              <Stat
                label="소비자 매출 (쿠팡가)"
                value={won(data.totals.consumer_revenue)}
                sub="쿠팡이 고객에게 판 금액 = 쿠팡의 매출"
              />
              <Stat
                label="우리 매출 (납품가)"
                value={won(data.totals.our_revenue)}
                tone="good"
                sub="판매수량 × 납품단가 = 우리 매출"
              />
              <Stat
                label="우리 몫"
                value={pct(data.totals.our_share)}
                sub={data.totals.our_share == null
                  ? undefined
                  : `쿠팡 마진 ${pct(String(1 - Number(data.totals.our_share)))}`}
              />
              <Stat
                label="광고비"
                value={won(data.totals.ad_spend)}
                sub={`RoAS ${ratio(data.totals.roas)} (우리 매출 기준)`}
              />
            </div>
            {!data.coverage.sales_data_covered && (
              <p className="mx-4 mb-3 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
                ⚠️ 이 기간엔 판매분석 수집분이 없어 <b>판매 축이 전부 「—」</b>입니다 —
                판매가 0이었던 게 아니라 <b>관측 불가</b>입니다.
                {data.coverage.sales_data_from &&
                  ` 판매분석은 ${data.coverage.sales_data_from} 이후만 있습니다.`}
                {" "}광고비·계산서 매출은 다른 원천이라 그대로 실측값입니다.
              </p>
            )}
            <p className="mx-4 mb-4 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
              ⚠️ 두 매출을 더하지 마세요 — 같은 물건이라 더하면 이중계상입니다. {data.axes_note}
            </p>
          </Card>

          <Card title="회계 정본(계산서)과의 관계">
            <div className="grid grid-cols-2 gap-4 px-4 py-4 md:grid-cols-3">
              <Stat label="계산서 매출 (sell-in)" value={won(data.totals.settlement_revenue)}
                sub="세금계산서 지급액 = 회계 정본" />
              <Stat label="우리 매출 (sell-through)" value={won(data.totals.our_revenue)}
                sub="고객에게 팔린 만큼" />
              <Stat
                label="납품단가 커버리지"
                value={pct(data.coverage.priced_pct)}
                tone={coverageTone}
                sub={`전량 ${num(data.coverage.qty_all)}개 중 단가 확인 ${num(data.coverage.qty_priced)}개 · 단가 미상 옵션 ${num(data.coverage.options_unpriced)}개`}
              />
            </div>
            <p className="mx-4 mb-4 text-xs text-gray-500">
              계산서(sell-in)와 판매(sell-through)는 <b>택일</b>입니다 — 더하면 같은 물건을 납품·판매
              두 번 세게 됩니다. 둘의 차이는 쿠팡 재고 증감입니다.
            </p>
          </Card>

          <Card
            title={`옵션별 매출 (${data.shown}/${data.option_count})`}
            right={data.shown < data.option_count
              ? <Badge tone="alert">{data.option_count - data.shown}개 생략됨</Badge>
              : undefined}
          >
            {data.options.length === 0 ? (
              <EmptyState reason="이 기간에 팔린 옵션이 없습니다." hint="기간을 넓혀 보세요." />
            ) : (
              <Table
                head={<>
                  <Th>옵션 / 상품</Th>
                  <Th right>판매량</Th>
                  <Th right>소비자 매출(쿠팡가)</Th>
                  <Th right>우리 매출(납품가)</Th>
                  <Th right>우리 몫</Th>
                  <Th right>납품단가</Th>
                  <Th right>방문자</Th>
                  <Th right>광고비</Th>
                  <Th right>RoAS</Th>
                </>}
              >
                {data.options.map((o) => <OptionRow key={o.option_id} o={o} />)}
              </Table>
            )}
            <p className="px-4 py-3 text-xs text-gray-500">
              「우리 매출」이 <b>—</b> 인 옵션은 납품단가를 못 붙인 것입니다(0원이 아닙니다).
              옵션 광고비는 Billboard(PA 기준)이라 계정 총액과 정의가 달라 합이 정확히 맞지
              않습니다 — 현재 차이 {won(data.ad_reconciliation.diff)}.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
