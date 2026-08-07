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
//   4) **손익 축은 우리 매출(납품가) 하나뿐이다.** 소비자 매출은 손익에 들어가지 않는다(D-CPP-2).
//      원가 커버리지가 100%가 아니면 그 손익은 **원가 확인분 부분집합**의 것이라고 화면이 말한다 —
//      미상분을 원가 0으로 넣어 전체인 척하면 이익이 부풀어 보인다(2026-08-06 실측 커버리지 48.9%).
import { useState } from "react";
import { Card, Stat, Table, Th, Td, Loading, EmptyState, Badge } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { FreshnessNote } from "../components/FreshnessNote";
import { fetchRocket1PRevenue, type Rocket1PRevenueOption, type Rocket1PDaily } from "../lib/api";
import { PeriodRangeBar } from "../components/PeriodRangeBar";
import { kstDate } from "../lib/periodRange";

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
      {/* ★원가 "—" = 등록원가를 못 붙인 SKU다. 0원이 아니다 — 0으로 보이면 그 옵션이
          공짜로 만든 물건처럼 읽히고, 순이익이 매출만큼 부풀어 보인다. */}
      <Td right>{won(o.cost)}</Td>
      <Td right>{won(o.ad_spend)}</Td>
      {/* ★원가를 몰라도 **부호가 확정**인 경우가 있다 — 광고비가 우리 매출을 넘으면 원가가
          얼마든 적자다. 그걸 "—"(모름)로 그리면, 적자가 빨강으로 칠해지는 표에서 빨강이
          없다는 것이 «문제 없음»으로 읽힌다(적대 리뷰 P1). 상한을 «≤»로 밝힌다. */}
      <Td right>
        {o.net_profit != null ? (
          <span className={Number(o.net_profit) >= 0 ? "font-medium text-judge-good" : "font-medium text-judge-bad"}>
            {won(o.net_profit)}
          </span>
        ) : o.net_profit_upper != null ? (
          <span className="font-medium text-judge-bad" title="원가 미상 — 원가를 0으로 놓은 상한값. 원가는 0 이상이므로 적자는 확정입니다.">
            ≤ {won(o.net_profit_upper)}
          </span>
        ) : NO_DATA}
      </Td>
      <Td right>{pct(o.profit_rate)}</Td>
      {/* ★분기선 아래면 빨강 — RoAS만 있으면 "1보다 크니 괜찮다"로 읽힌다(라이브에
          RoAS 1.08인데 이익률 −22.1%인 행이 있었다). 판정 기준이지 권고가 아니다. */}
      <Td right>
        <span className={o.roas != null && o.bep_roas != null && Number(o.roas) < Number(o.bep_roas)
          ? "font-medium text-judge-bad" : ""}>
          {ratio(o.roas)}
        </span>
      </Td>
      <Td right><span className="text-gray-400">{ratio(o.bep_roas)}</span></Td>
    </tr>
  );
}

/** 일별 손익 한 줄.
 *  ★「우리 매출」(전량)과 「손익 기준 매출」(원가 확인분)을 **나란히** 둔다 — 이익률의 분모가
 *    후자라서, 앞의 것만 보이면 이익률이 왜 그 값인지 설명되지 않는다. */
function DailyRow({ d }: { d: Rocket1PDaily }) {
  const net = d.net_profit == null ? null : Number(d.net_profit);
  const cov = d.cost_coverage == null ? null : Number(d.cost_coverage);
  const partial = cov != null && cov < 1;
  return (
    <tr className="hover:bg-gray-50">
      <Td>{d.date}</Td>
      <Td right>{num(d.qty)}</Td>
      <Td right>{won(d.consumer_revenue)}</Td>
      <Td right><span className="font-medium text-blue-700">{won(d.our_revenue)}</span></Td>
      <Td right>{won(d.ad_spend_all)}</Td>
      <Td right>
        <span className={partial ? "text-amber-700" : "text-gray-400"}>{pct(d.cost_coverage)}</span>
      </Td>
      {/* ★여기부터 전부 **원가 확인분** 축이다 — 왼쪽 전량 값과 분모가 다르다.
          섞으면 행 산술이 안 맞고 부가세가 음수로 뜬다(2026-08-07 라이브 결함). */}
      <Td right><span className="border-l border-gray-200 pl-2">{won(d.pnl_revenue)}</span></Td>
      <Td right>{won(d.cost)}</Td>
      <Td right>{won(d.promo_burden)}</Td>
      <Td right>{won(d.ad_spend)}</Td>
      <Td right>{won(d.vat)}</Td>
      <Td right>
        <span className={net == null ? "" : net >= 0 ? "font-medium text-judge-good" : "font-medium text-judge-bad"}>
          {won(d.net_profit)}
        </span>
      </Td>
      <Td right>{pct(d.profit_rate)}</Td>
    </tr>
  );
}

/** 손익 사다리 한 줄. 부호(−)를 라벨에 박아 무엇이 빠지는 값인지 눈으로 보이게 한다. */
function PnlLine({ label, value, sub, sign, strong }: {
  label: string; value: string; sub?: string; sign?: "minus" | "eq"; strong?: boolean;
}) {
  return (
    <div className={`flex items-baseline justify-between gap-3 py-1.5 ${strong ? "border-t border-gray-200 pt-2.5" : ""}`}>
      <div className="min-w-0">
        <span className={`text-sm ${strong ? "font-semibold text-gray-900" : "text-gray-600"}`}>
          {sign === "minus" ? "− " : sign === "eq" ? "= " : ""}{label}
        </span>
        {sub && <span className="ml-2 text-xs text-gray-400">{sub}</span>}
      </div>
      <div className={`tabular-nums ${strong ? "text-lg font-bold text-gray-900" : "text-sm text-gray-800"}`}>
        {value}
      </div>
    </div>
  );
}

export default function Rocket1PRevenue() {
  const [from, setFrom] = useState(kstDate(-6));
  const [to, setTo] = useState(kstDate(0));

  const { data, error } = useAsyncData(
    () => fetchRocket1PRevenue({ from, to, limit: 300 }),
    [from, to],
  );

  const priced = data?.coverage.priced_pct;
  const coverageTone = priced == null ? "neutral" : Number(priced) >= 0.95 ? "neutral" : "warn";

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-gray-900">로켓배송(1P) 매출·손익 — 소비자가 ∥ 납품가</h1>
        <p className="mt-1 text-sm text-gray-500">
          쿠팡이 고객에게 판 금액과 우리가 쿠팡에 판 금액을 나란히 보고, 우리 매출(납품가)에서
          비용을 빼 손익까지 봅니다. 조회 전용이며, 이 화면의 값은 종합조망 순이익에
          결합되지 않습니다.
        </p>
      </div>

      {/* ★축이 「판매일」이다 — 통합 대사(발주일)와 **다른 축**이라 이름을 박는다.
          그리고 판매분석의 두 한계(롤링 약 2개월 · 당일·전일치 없음)를 여기서 말한다:
          안 그러면 '오늘'을 눌러 빈 화면을 보고 «수집 실패»로 오독한다. */}
      <PeriodRangeBar
        label="판매일"
        from={from} to={to} onFrom={setFrom} onTo={setTo}
        note={<>
          기간은 <b>판매일(KST)</b> 기준입니다 — 고객에게 실제로 팔린 날이지 발주·정산일이
          아닙니다(발주 축은 「발주·정산 대사」 화면입니다).
          {" "}<b>판매분석은 당일·전일치를 주지 않습니다</b> — &lsquo;오늘·어제&rsquo;는 대개 비어
          있는 것이 정상이고 수집 실패와 다릅니다.
          {data?.coverage.sales_data_from && (
            <> 그리고 쿠팡 판매분석은 <b>{data.coverage.sales_data_from}부터만</b> 있습니다
              (롤링 약 2개월) — 그 이전을 조회하면 판매 축이 전부 「—」가 되고, 계산서 매출·
              광고비는 다른 원천이라 그대로 나옵니다.</>
          )}
        </>}
      />

      {error && (
        <Card><EmptyState reason="매출 데이터를 불러오지 못했습니다." hint={String(error)} /></Card>
      )}
      {!data && !error && <Card><Loading /></Card>}

      {data && (
        <>
          {/* ── 두 축을 나란히. 사이에 「우리 몫」을 놓아 왜 다른지 한 눈에 보이게 한다 ── */}
          <Card title="매출 두 축">
            {/* ★"이게 무슨 데이터냐"에 화면이 먼저 답한다(Jino 2026-08-07). 하루치로 오해하기
                쉬운 자리다 — 원천·그레인·집계 방식·기간을 한 줄에 박는다. */}
            <p className="mx-4 mt-3 rounded bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-600">
              <b>{data.period.from} ~ {data.period.to}</b>({data.freshness.days_expected}일 요청)
              <b> 기간 합계</b>입니다 — 하루치가 아닙니다.
              원천은 쿠팡 <b>판매분석의 옵션×일 실적</b>(고객에게 실제로 팔린 수량·금액)이고,
              그 기간에 <b>데이터가 들어온 날</b>의 모든 옵션을 더한 값입니다. 「계산서 매출」만
              다른 원천(세금계산서)이며, 광고비는 광고 계정 리포트입니다.
              {/* ★"모든 날"이라고 쓰면 안 된다(적대 리뷰 P1) — 판매분석은 당일·전일치를 주지
                  않아 기본 창(7일)엔 늘 5~6일만 들어온다. 아래 FreshnessNote가 실제 일수를
                  말하는데, 이 문단이 "모든 날"이라고 확정해 주면 그 경고를 덮어버린다. */}
              {data.freshness.days_no_data > 0 && (
                <> <b className="text-amber-700">
                  요청 {data.freshness.days_expected}일 중 실제로 들어온 건
                  {" "}{data.freshness.days_with_data}일치입니다
                </b> — 아래 합계를 {data.freshness.days_expected}일치로 읽으면 안 됩니다.</>
              )}
            </p>
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

          {/* ── 손익 ── 축은 **우리 매출(납품가)** 하나뿐이다. 소비자 매출은 여기 안 들어온다. ── */}
          <Card
            title="우리 손익 (납품가 축)"
            right={
              <div className="flex items-center gap-2">
                {/* ★배지는 basis(구조 판정)를 따른다 — 반올림된 비율을 쓰면 0.99996이 100%가
                    되어 「기간 전체」와 「원가 미등록 SKU N개」가 동시에 뜬다(적대 리뷰 P2). */}
                {data.pnl.basis === "full"
                  ? <Badge tone="neutral">원가 확인 100% · 기간 전체</Badge>
                  : data.pnl.cost_coverage == null
                    ? undefined
                    : <Badge tone="alert">원가 확인 {pct(data.pnl.cost_coverage)}분만</Badge>}
                {/* ★새 창 — 지금 보고 있는 기간을 URL에 실어 그 창을 그대로 재현·공유한다
                    (Jino: "우리 손익이 정말 실수 없이 나오는지 어떻게 확신할 수 있는지"). */}
                <a href={`/rocket-1p/pnl-audit?from=${from}&to=${to}`} target="_blank" rel="noreferrer"
                   className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50">
                  근거 보기 ↗
                </a>
              </div>
            }
          >
            {(() => {
              const p = data.pnl;
              /* ★사유를 화면이 추측하지 않는다 — backend가 정한다(적대 리뷰 P2). 예전엔
                 우선순위가 조금만 틀려도 사용자를 엉뚱한 작업으로 보냈다. */
              const blocked = p.blocked;
              const partial = p.basis === "costed_subset";
              const noSales = Number(p.ad_no_sales);
              return (
                <>
                  {blocked ? (
                    <div className="px-4 py-4">
                      <EmptyState reason="이 기간의 손익을 낼 수 없습니다." hint={blocked.reason} />
                    </div>
                  ) : (
                    <>
                      {partial && (
                        <p className="mx-4 mt-3 rounded bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
                          ⚠️ 아래 손익은 <b>원가가 등록된 SKU만</b> 더한 값입니다
                          (우리 매출 {won(p.revenue_priced)} 중 <b>{won(p.revenue)}</b> 어치).
                          매출·원가·분담금·광고비를 <b>전부 같은 부분집합으로 맞춰</b> 냈기 때문에
                          이 범위 안에서는 참인 숫자지만, <b>기간 전체의 손익은 아닙니다.</b>{" "}
                          원가 미상분을 0원으로 넣어 전체인 척하면 이익이 그만큼 부풀어 보이므로
                          그렇게 하지 않습니다.
                        </p>
                      )}
                      <div className="px-4 py-3">
                        <PnlLine label="우리 매출 (납품가)" value={won(p.revenue)}
                          sub={`판매 ${num(p.qty)}개`} />
                        <PnlLine label="원가" value={won(p.cost)} sign="minus"
                          sub="SellC 등록원가 · 부가세 포함 축" />
                        <PnlLine label="프로모션 분담금" value={won(p.promo_burden)} sign="minus"
                          sub={Number(p.promo_burden) === 0
                            ? "이 기간에 걸친 프로모션의 할인액 합 (0 = 수집된 프로모션 없음)"
                            : "제안서에서 받은 할인액 (실측 13건 전부 전액 우리 부담)"} />
                        <PnlLine label="광고비" value={won(p.ad_spend)} sign="minus"
                          sub={partial
                            ? "옵션 그레인(Billboard) · 위 부분집합 옵션분만"
                            : "옵션 그레인(Billboard)"} />
                        {/* ★그 창에 판매행이 없는 옵션의 광고비. 예전 판은 이 돈을 손익에
                            한 번도 넣지 않으면서 화면에 보여주지도 않았다 — 라이브 282,794원이
                            통째로 사라졌고, basis='full'에서는 부호까지 뒤집혔다(적대 리뷰 P1). */}
                        {noSales > 0 && (
                          <PnlLine
                            label={p.ad_no_sales_included
                              ? "광고비 (그 기간 판매 없는 옵션)"
                              : "광고비 (그 기간 판매 없는 옵션) — 위 손익에 미포함"}
                            value={won(p.ad_no_sales)}
                            sign={p.ad_no_sales_included ? "minus" : undefined}
                            sub={p.ad_no_sales_included
                              ? "광고는 돌았는데 그 기간에 안 팔린 옵션"
                              : "귀속할 판매가 없어 부분집합에 섞지 않았습니다 — 실제로 나간 돈입니다"} />
                        )}
                        <PnlLine label="부가세 (납부세액)" value={won(p.vat)} sign="minus"
                          sub="매출VAT − 매입세액공제" />
                        <PnlLine label="순이익" value={won(p.net_profit)} sign="eq" strong
                          sub={`이익률 ${pct(p.profit_rate)}`} />
                      </div>
                      {partial && (
                        <p className="mx-4 mb-3 text-xs leading-relaxed text-gray-500">
                          ★<b>이익률 {pct(p.profit_rate)}를 전체 매출에 곱하지 마세요.</b> 빠진
                          부분집합은 무작위가 아닙니다 — 매출·광고비가 큰 SKU가 통째로 빠져 있을
                          수 있습니다(현재 1위 미등록 SKU가 그렇습니다).
                        </p>
                      )}
                    </>
                  )}

                  {/* ★"—"로 끝내지 않는다 — 무엇을 등록하면 채워지는지 이름으로 말한다.
                      단 ①분담금 때문에 막힌 상태면 원가를 등록해도 안 풀리므로 그렇게 말하고
                      ②`ignored`(이미 "제외"로 결정한) SKU는 목록에서 뺀다 — 재제안 방지가
                      그 상태의 존재 이유다(적대 리뷰 P2). */}
                  {p.uncosted.actionable_skus > 0 && (
                    <div className="border-t border-gray-100 px-4 py-3">
                      <div className="text-sm font-medium text-gray-800">
                        원가가 안 붙은 SKU {p.uncosted.actionable_skus}개 · 우리 매출{" "}
                        {won(p.uncosted.our_revenue)}
                        {p.uncosted.our_revenue_partial && " 이상"}
                      </div>
                      {/* ★★할 일을 「원가 등록」 하나로 뭉뚱그리지 않는다(2026-08-07 실사고).
                          예전 문구를 그대로 따라 원가를 등록했는데 화면이 안 움직였다 — 그
                          SKU들은 원가가 없는 게 아니라 **연결이 없었다**(라이브 178건). 원가는
                          내부 SKU에 붙고 판매는 쿠팡 상품번호로 들어와서, 다리가 없으면 원가를
                          아무리 넣어도 안 붙는다. */}
                      <p className="mt-0.5 text-xs leading-relaxed text-gray-500">
                        {/* ★신규를 맨 앞에 말한다 — 새 폰이 나올 때마다 생기고 나오자마자
                            매출 1위가 되는데, 매출 순 목록에선 옛 꼬리에 묻힌다. */}
                        {p.uncosted.new_skus > 0 && (
                          <><b className="text-amber-800">
                            ★최근 {p.uncosted.new_sku_window_days}일 안에 <b>새로 나온</b> 상품
                            {" "}{p.uncosted.new_skus}개가 아직 연결 안 됐습니다</b>
                            (우리 매출 {won(p.uncosted.new_our_revenue)}) — 아래 목록 맨 위에
                            「신규」로 표시했습니다. 새 기종이 나오면 곧바로 매출 상위가 되므로
                            이것부터 이어주세요. (판별은 <b>발주에 처음 등장한 날</b> 기준입니다 —
                            "안 팔리던 게 이제 팔린다"와는 다릅니다.){" "}</>
                        )}
                        {p.uncosted.link_missing_skus > 0 && (
                          <><b className="text-amber-800">
                            {p.uncosted.link_missing_skus}개는 「연결」이 없습니다</b> — 쿠팡
                            상품번호와 내부 SKU가 이어져 있지 않아 <b>원가를 등록해도 안 붙습니다.</b>{" "}
                            <a href="/command-center" className="underline">「종합 조망」 화면의
                            '원가 매핑 관리'</a>에서 연결하세요.{" "}</>
                        )}
                        {p.uncosted.cost_missing_skus > 0 && (
                          <><b>{p.uncosted.cost_missing_skus}개는 연결은 돼 있고 원가만 없습니다</b> —
                            SellC에 원가를 등록하면 됩니다.{" "}</>
                        )}
                        {p.uncosted.excluded_skus > 0 && (
                          <>이와 별개로 <b>「원가 제외」로 이미 결정된 SKU {p.uncosted.excluded_skus}개</b>가
                            팔렸습니다(샘플·증정 등) — 위 목록에서 뺐고, 그래서 커버리지 100%는 위
                            조치만으로는 달성되지 않습니다. <b>아래에 따로 적었습니다.</b>{" "}</>
                        )}
                        {blocked?.code === "promo_burden_unknown" && (
                          <><b>다만 지금 손익이 막힌 이유는 원가가 아니라 프로모션 제안서</b>입니다 —
                            위 조치를 다 해도 그것부터 들어와야 손익이 나옵니다.{" "}</>
                        )}
                        {p.uncosted.our_revenue_partial &&
                          "일부 SKU는 납품단가도 없어 우리 매출을 몰라 위 합계에서 빠졌습니다(0이 아닙니다)."}
                      </p>
                      <Table
                        head={<>
                          <Th>SKU / 상품</Th>
                          <Th right>판매량</Th>
                          <Th right>우리 매출(납품가)</Th>
                          <Th right>소비자 매출</Th>
                          <Th right>할 일</Th>
                        </>}
                      >
                        {p.uncosted.top.map((u) => (
                          <tr key={u.sku_id ?? u.product_name} className="hover:bg-gray-50">
                            <Td>
                              <div className="max-w-md truncate text-gray-900"
                                title={u.product_name ?? u.sku_id ?? ""}>
                                {u.product_name ?? u.sku_id}
                              </div>
                              <div className="mt-0.5 text-xs text-gray-400">
                                SKU {u.sku_id ?? "—"}
                                {u.first_po_at && ` · 발주 첫 등장 ${u.first_po_at}`}
                                {u.first_sold_at && ` · 판매 첫 관측 ${u.first_sold_at}`}
                                {/* ★관측 한계를 숨기지 않는다 — 그 전은 롤링창 밖이라 모른다. */}
                                {u.first_sold_at_bounded && !u.first_po_at && " (그 전은 관측 없음)"}
                              </div>
                            </Td>
                            <Td right>{num(u.qty)}</Td>
                            <Td right>{won(u.our_revenue)}</Td>
                            <Td right>{won(u.consumer_revenue)}</Td>
                            <Td right>
                              {u.is_new && (
                                <span className="mr-1"><Badge tone="owner">신규</Badge></span>
                              )}
                              {u.reason === "no_link"
                                ? <Badge tone="alert">연결 필요</Badge>
                                : <Badge tone="neutral">원가 등록</Badge>}
                              {u.loss_confirmed && (
                                <span className="ml-1"><Badge tone="alert">확정 적자</Badge></span>
                              )}
                            </Td>
                          </tr>
                        ))}
                      </Table>
                      {p.uncosted.loss_confirmed_skus > 0 && (
                        <p className="mt-2 text-xs text-amber-800">
                          ⚠️ 「확정 적자」는 <b>원가를 몰라도 손해가 확정된</b> SKU입니다 — 광고비가
                          이미 우리 매출을 넘었습니다. 원가는 0 이상이므로 부호는 바뀌지 않습니다.
                        </p>
                      )}
                      {p.uncosted.actionable_skus > p.uncosted.top.length && (
                        <p className="mt-2 text-xs text-gray-400">
                          매출 상위 {p.uncosted.top.length}개만 표시 · 총 {p.uncosted.actionable_skus}개
                        </p>
                      )}
                    </div>
                  )}

                  {/* ★「원가 제외」 결정도 늙는다 — 그때는 샘플·증정이었어도 지금은 정상
                      판매일 수 있다. 개수만 세면 재검토할 방법이 없어 이름으로 적는다.
                      ★작업 목록과 **분리**한다: 여기 있는 건 «시키는 것»이 아니라
                      «이 결정이 아직 맞나»를 보는 자리다. */}
                  {p.uncosted.excluded_top.length > 0 && (
                    <div className="border-t border-gray-100 px-4 py-3">
                      <div className="text-sm font-medium text-gray-700">
                        「원가 제외」로 결정된 SKU {p.uncosted.excluded_skus}개가 팔렸습니다 ·
                        우리 매출 {won(p.uncosted.excluded_our_revenue)}
                      </div>
                      <p className="mt-0.5 text-xs text-gray-500">
                        샘플·증정 등으로 <b>원가를 안 붙이기로 이미 결정한 것</b>이라 위 작업
                        목록에서 뺐습니다. 다만 그 결정이 아직 맞는지는 <b>가끔 봐야 합니다</b> —
                        정상 판매로 바뀐 게 섞여 있으면 그만큼 손익이 안 잡힙니다.
                        고치려면 「종합 조망」의 '원가 매핑 관리'에서 제외를 풀고 연결하세요.
                      </p>
                      <Table
                        head={<>
                          <Th>SKU / 상품</Th>
                          <Th right>판매량</Th>
                          <Th right>우리 매출(납품가)</Th>
                          <Th right>소비자 매출</Th>
                        </>}
                      >
                        {p.uncosted.excluded_top.map((u) => (
                          <tr key={u.sku_id ?? u.product_name} className="hover:bg-gray-50">
                            <Td>
                              <div className="max-w-md truncate text-gray-700"
                                title={u.product_name ?? u.sku_id ?? ""}>
                                {u.product_name ?? u.sku_id}
                              </div>
                              <div className="mt-0.5 text-xs text-gray-400">SKU {u.sku_id ?? "—"}</div>
                            </Td>
                            <Td right>{num(u.qty)}</Td>
                            <Td right>{won(u.our_revenue)}</Td>
                            <Td right>{won(u.consumer_revenue)}</Td>
                          </tr>
                        ))}
                      </Table>
                      {p.uncosted.excluded_skus > p.uncosted.excluded_top.length && (
                        <p className="mt-2 text-xs text-gray-400">
                          매출 상위 {p.uncosted.excluded_top.length}개만 표시 · 총 {p.uncosted.excluded_skus}개
                        </p>
                      )}
                    </div>
                  )}

                  {/* ★두 광고비가 왜 다른지에 **맞는 이유**를 댄다(적대 리뷰 P1). 예전 문구는
                      "그레인 정의 차이"라고만 했는데, 라이브에서 그 몫은 0.12%뿐이고 실제 차이의
                      58%는 부분집합 제한이었다 — 화면이 지배적 원인을 빼고 사소한 원인만 지목했다. */}
                  <p className="mx-4 mb-4 mt-1 text-xs leading-relaxed text-gray-500">
                    순이익 = 우리 매출 − 원가 − 프로모션 분담금 − 광고비 − 납부세액.
                    <b> 소비자 매출(쿠팡가)은 손익에 들어가지 않습니다</b> — 그건 쿠팡의 매출이지
                    우리 돈이 아닙니다.
                    {p.ad_spend != null && (
                      <> 위 「매출 두 축」의 광고비는 <b>계정 총액 {won(p.ad_account_total)}</b>인데
                        이 사다리는 <b>{won(p.ad_spend)}</b>입니다 — 차이의 대부분은
                        {partial ? " 위 부분집합 제한(원가 등록된 옵션만)" : " 그레인 차이"}이고,
                        여기에 그 기간 판매 없는 옵션분 {won(p.ad_no_sales)}
                        {p.ad_no_sales_included ? "(포함됨)" : "(미포함)"},
                        그리고 Billboard(PA)와 report/SALES의 그레인 차이가 더해집니다.</>
                    )}
                  </p>
                </>
              );
            })()}
          </Card>

          {/* ── 일별 손익 (Jino 2026-08-07: "우리의 일일 손익을 보고 싶은거야") ──
              ★일별 순이익의 합 = 위 손익 사다리 = 옵션별 표의 합. 셋 다 같은 「날짜×옵션」
                원자를 다른 방향으로 접은 것이라 원 단위까지 같다. */}
          {data.daily.length > 0 && (
            <Card
              title={`일별 손익 (${data.daily.length}일)`}
              right={data.freshness.days_no_data > 0
                ? <Badge tone="alert">요청 {data.freshness.days_expected}일 중 {data.daily.length}일치</Badge>
                : undefined}
            >
              <div className="overflow-x-auto">
                <Table
                  head={<>
                    <Th>날짜</Th>
                    <Th right>판매수량</Th>
                    <Th right>소비자 매출</Th>
                    <Th right>우리 매출(전량)</Th>
                    <Th right>광고비(전량)</Th>
                    <Th right>원가 확인</Th>
                    <Th right>손익기준 매출</Th>
                    <Th right>원가</Th>
                    <Th right>분담금</Th>
                    <Th right>광고비</Th>
                    <Th right>부가세</Th>
                    <Th right>순이익</Th>
                    <Th right>이익률</Th>
                  </>}
                >
                  {data.daily.map((d) => <DailyRow key={d.date} d={d} />)}
                </Table>
              </div>
              <p className="px-4 py-3 text-xs leading-relaxed text-gray-500">
                ★<b>「원가 확인」 열 오른쪽은 전부 그날 원가가 붙은 SKU만</b>의 값입니다 —
                매출·원가·분담금·광고비·부가세·순이익이 <b>같은 부분집합</b>이라 행 산술이
                스스로 맞습니다. 왼쪽의 「우리 매출(전량)」·「광고비(전량)」은 그날 전부이고,
                <b>둘은 분모가 다릅니다.</b> 이익률은 오른쪽 기준이니, 커버리지가 100%가 아닌
                날의 이익률을 전체에 적용하면 안 됩니다.
                <br />
                <b>1P는 판매수수료·배송비가 없습니다</b>(쿠팡 부담) — 그래서 그 열이 없습니다.
                차감되는 비용은 원가·프로모션 분담금·광고비·납부세액 넷뿐입니다.
                {data.daily.some((d) => Number(d.ad_no_sales) > 0) && (
                  <> 광고비 열엔 <b>그날 판매가 없던 옵션의 광고비가 빠져 있습니다</b>
                    (기간 합 {won(data.pnl.ad_no_sales)}) — 귀속할 판매가 없어 위 손익에
                    섞지 않았지만 실제로 나간 돈입니다.</>
                )}
                {" "}날짜가 빠진 날은 판매분석이 그날치를 아직 안 준 것이지 판매 0이 아닙니다.
              </p>
            </Card>
          )}

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
                  <Th right>원가</Th>
                  <Th right>광고비</Th>
                  <Th right>순이익</Th>
                  <Th right>이익률</Th>
                  <Th right>RoAS</Th>
                  <Th right>손익분기 RoAS</Th>
                </>}
              >
                {data.options.map((o) => <OptionRow key={o.option_id} o={o} />)}
              </Table>
            )}
            <p className="px-4 py-3 text-xs leading-relaxed text-gray-500">
              「우리 매출」이 <b>—</b> 인 옵션은 납품단가를 못 붙인 것이고, 「원가」가 <b>—</b> 인
              옵션은 SellC 등록원가를 못 붙인 것입니다 — <b>둘 다 0원이 아니라 «모름»</b>이라,
              그 옵션은 순이익도 내지 않습니다. 단 순이익이 <b>「≤ −금액」</b>으로 뜨는 행은
              원가를 몰라도 <b>적자가 확정</b>인 것입니다(광고비가 이미 우리 매출을 넘었습니다). 방문자·전환은 「유입·전환 퍼널」 화면에 있습니다.
              「손익분기 RoAS」는 <b>매출 ÷ (매출−원가−분담금)</b>입니다 — 실제 RoAS가 이보다
              낮으면 그 옵션은 적자라 RoAS를 빨강으로 칠합니다. <b>—</b>인 것은 원가를 모르거나
              공헌이익이 0 이하라 <b>어떤 RoAS로도 흑자가 안 되는</b> 경우입니다.
              옵션 광고비는 Billboard(PA 기준)이라 계정 총액과 정의가 달라 합이 정확히 맞지
              않습니다 — 현재 차이 {won(data.ad_reconciliation.diff)}.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
