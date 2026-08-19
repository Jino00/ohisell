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
        {/* ★한 줄 truncate였다 — 이 상품명들은 「필름 2p + EZ 툴 세트, 아이폰17 Pro, 1세트」처럼
            뒤쪽에 **기종·수량**이 붙어서 잘리면 서로 구분이 안 됐다(2026-08-19 Jino 지적).
            2줄까지 펼치고, 그래도 넘치면 말줄임 + title로 전문을 남긴다. */}
        <div className="max-w-md line-clamp-2 break-words text-gray-900" title={o.product_name ?? o.option_id}>
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
              /* ★광고 원장의 나머지 두 통. 예전 판은 이 둘을 화면에 **한 번도 안 그렸다** —
                 구멍1(원가 미상 옵션분)은 어느 줄에도 없었고, 구멍2(팔린 옵션의 판매 없는
                 날)는 근거 화면 A7이 fail로만 고발하고 있었다. 안 보이는 돈은 없는 돈이 된다. */
              const noSalesDays = Number(p.ad_no_sales_days);
              const adUncosted = Number(p.ad_uncosted);
              // ★D-CPP-38 — 구조적 두 통에 실제로 곱해진 비율(%). null이면 판정 불가.
              //   종전엔 0 아니면 100뿐이었다(절벽). 이제 커버리지 비율이다.
              const foldPct = p.ad_deduct_share === null || p.ad_deduct_share === undefined
                ? null : Math.round(Number(p.ad_deduct_share) * 1000) / 10;
              const outOfRange = Number(p.ad_out_of_range ?? 0);
              /* ★«손익 밖에 남은 돈»은 `ad_unattributed`와 **다르다**: included면 뒤 두 통이
                 이미 순이익에서 빠졌으므로 남은 것은 구멍1뿐이다. 두 값을 같이 쓰면 각주가
                 사다리를 부정한다(적대 리뷰 P1-2). 술어는 사다리 부호와 같은 것을 쓴다. */
              /* ★2026-08-11(D-CPP-38): 술어가 불리언이 아니라 **비율**이 됐고 통이 5개가 됐다.
                 「손익 밖에 남은 돈」 = 원장 − 사다리에 실린 광고비. 그게 정의상 정확하고,
                 통을 하나 더 만들 때마다 이 식을 고칠 필요가 없다(그게 이번 P1-1의 원인이었다). */
              const outsidePnl = Number(p.ad_option_total) - Number(p.ad_spend);
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
                            : "옵션 그레인(Billboard) · 아래 두 통 포함"} />
                        {/* ★★구멍1 — 팔렸는데 손익에 못 들어간 옵션의 광고비. 예전 판에서
                            이 돈은 **화면 어느 줄에도 없었다**(라이브 08-08 33,750원 = 계정
                            총액의 4.2%). 아래 각주가 "차이의 대부분은 부분집합 제한"이라는
                            말로 대신하고 있었는데, 말은 금액이 아니다. */}
                        {adUncosted > 0 && (
                          <PnlLine
                            label="광고비 (원가 미상 옵션) — 위 손익에 미포함"
                            value={won(p.ad_uncosted)}
                            sub="이 옵션들은 매출도 손익에 안 들어갔습니다 — 비용만 빼면 이익이 거꾸로 위조됩니다. 아래 목록을 해소하면 위 줄로 옮겨갑니다" />
                        )}
                        {/* ★★구멍2 — 팔린 옵션이 «안 팔린 날»에 쓴 광고비. 원자는 판매행에서만
                            나오고 ad_no_sales는 옵션 단위로 판정해서, 판정 그레인이 다른 그
                            사이로 새던 돈이다(창 07-31~08-06 435,916원). 하루 창에선 0이다. */}
                        {noSalesDays > 0 && (
                          <PnlLine
                            label={foldPct === null ? "광고비 (팔린 옵션의 판매 없는 날)"
                              : foldPct >= 100 ? "광고비 (팔린 옵션의 판매 없는 날)"
                              : `광고비 (팔린 옵션의 판매 없는 날) — ${foldPct}%만 반영`}
                            value={won(p.ad_no_sales_days)}
                            sign={foldPct !== null && foldPct > 0 ? "minus" : undefined}
                            sub="그 옵션은 기간 안에 팔렸지만 광고를 쓴 그 날엔 판매행이 없습니다 — 귀속할 원자가 없는 돈입니다" />
                        )}
                        {/* ★구멍3 — 그 창에 판매행이 없는 옵션의 광고비. 예전 판은 이 돈을 손익에
                            한 번도 넣지 않으면서 화면에 보여주지도 않았다 — 라이브 282,794원이
                            통째로 사라졌고, basis='full'에서는 부호까지 뒤집혔다(적대 리뷰 P1). */}
                        {noSales > 0 && (
                          <PnlLine
                            label={foldPct === null || foldPct >= 100
                              ? "광고비 (그 기간 판매 없는 옵션)"
                              : `광고비 (그 기간 판매 없는 옵션) — ${foldPct}%만 반영`}
                            value={won(p.ad_no_sales)}
                            sign={foldPct !== null && foldPct > 0 ? "minus" : undefined}
                            sub="광고는 돌았는데 그 기간에 안 팔린 옵션" />
                        )}
                        {/* ★★구멍0 — 판매분석 롤링 창보다 **앞선 날**의 광고비(D-CPP-38).
                            「안 팔림」이 아니라 «관측 불가»다. 라이브 창 05-14~08-11에서
                            12,969,126원이 여기 있었고, 종전엔 구멍2·3에 섞여 「광고했는데
                            안 팔림 34.3%」를 만들고 있었다 — 그 3분의 2가 다른 것이었다.
                            ★차감하지 않는다: 매출을 관측조차 못 하는 구간의 비용만 빼면
                            이익이 한쪽으로 위조된다(구멍1을 안 빼는 것과 같은 이유). */}
                        {outOfRange > 0 && (
                          <PnlLine
                            label="광고비 (판매분석이 없는 기간) — 위 손익에 미포함"
                            value={won(p.ad_out_of_range)}
                            sub="쿠팡 판매분석은 롤링 약 2개월이라 그 이전 날은 «안 팔림»이 아니라 «관측 불가»입니다 — 팔렸는지 자체를 알 수 없어 차감하지 않습니다" />
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
                      ②`excluded`(연결 안 하기로 정한) SKU는 목록에서 뺀다 — 재제안 방지가
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
                          <>이와 별개로 <b>「연결 안 함」으로 찍힌 SKU {p.uncosted.excluded_skus}개</b>가
                            팔렸습니다 — 위 목록에서 뺐고, 그래서 커버리지 100%는 위 조치만으로는
                            달성되지 않습니다. <b>그중 일부는 «결정»이 아니라 자동 매핑 실패일 수
                            있습니다</b> — 아래에 기록된 사유와 함께 따로 적었습니다.{" "}</>
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
                              <div className="max-w-md line-clamp-2 break-words text-gray-900"
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

                  {/* ★「연결 안 함」 결정도 늙는다 — 그때는 샘플·증정이었어도 지금은 정상
                      판매일 수 있다. 개수만 세면 재검토할 방법이 없어 이름으로 적는다.
                      ★작업 목록과 **분리**한다: 여기 있는 건 «시키는 것»이 아니라
                      «이 결정이 아직 맞나»를 보는 자리다. */}
                  {p.uncosted.excluded_top.length > 0 && (
                    <div className="border-t border-gray-100 px-4 py-3">
                      <div className="text-sm font-medium text-gray-700">
                        「연결 안 함」으로 찍혀 있는 SKU {p.uncosted.excluded_skus}개가 팔렸습니다 ·
                        우리 매출 {won(p.uncosted.excluded_our_revenue)}
                      </div>
                      {/* ★★"이미 결정된 것"이라고 **단정하지 않는다**(2026-08-09 실사고).
                          prod의 옛 `ignored` 22건이 전부 `note='no suggestion or low score'`,
                          즉 «샘플·증정으로 결정»이 아니라 **이름 유사도 매칭이 실패한 것**을
                          같은 status에 넣어 둔 것이었다(ref 47 §2). 화면이 그걸 결정이라
                          부르며 "그냥 두세요"로 안내하면, 정상 판매 상품이 영영 손익 밖에 남는다.
                          status가 두 의미를 겸하는 한 화면이 할 수 있는 정직한 일은
                          **기록된 사유를 그대로 보여 주는 것**이다. */}
                      <p className="mt-0.5 text-xs text-gray-500">
                        「연결 안 함」으로 표시돼 있어 위 작업 목록에서 뺐습니다. ★이 표시는 <b>원가가 0원이라는 뜻이 아니라</b> 「모름」이라, 그 상품은 손익 계산에서 빠집니다. ★다만 이 표시는
                        <b> 두 가지를 겸하고 있습니다</b> — 샘플·증정처럼 <b>실제로 결정한 것</b>과,
                        자동 매핑이 후보를 못 찾아 <b>그냥 제외로 찍힌 것</b>. 오른쪽 「기록된 사유」를
                        보고 가르세요(<code className="text-[11px]">no suggestion or low score</code>
                        는 <b>결정이 아니라 매칭 실패</b>입니다 — 그건 이어주면 손익에 들어옵니다).
                        고치려면 「종합 조망」의 '원가 매핑 관리'에서 제외를 풀고 연결하세요.
                      </p>
                      <Table
                        head={<>
                          <Th>SKU / 상품</Th>
                          <Th right>판매량</Th>
                          <Th right>우리 매출(납품가)</Th>
                          <Th right>소비자 매출</Th>
                          <Th>기록된 사유</Th>
                        </>}
                      >
                        {p.uncosted.excluded_top.map((u) => (
                          <tr key={u.sku_id ?? u.product_name} className="hover:bg-gray-50">
                            <Td>
                              <div className="max-w-md line-clamp-2 break-words text-gray-700"
                                title={u.product_name ?? u.sku_id ?? ""}>
                                {u.product_name ?? u.sku_id}
                              </div>
                              <div className="mt-0.5 text-xs text-gray-400">SKU {u.sku_id ?? "—"}</div>
                            </Td>
                            <Td right>{num(u.qty)}</Td>
                            <Td right>{won(u.our_revenue)}</Td>
                            <Td right>{won(u.consumer_revenue)}</Td>
                            <Td>
                              {u.excluded_note
                                ? <span className="text-xs text-gray-600">{u.excluded_note}</span>
                                /* ★사유가 없으면 «사유 없음»이 아니라 «기록 안 됨»이다.
                                   빈 칸으로 두면 «결정에 이유가 있었다»로 읽힌다. */
                                : <span className="text-xs text-gray-400">사유 기록 없음</span>}
                            </Td>
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
                    {/* ★★각주가 «대부분»으로 얼버무리지 않는다(2026-08-09). 옵션 광고 원장은
                        네 통으로 **남김없이** 갈라지고 그 합이 원 단위로 맞는다 — 그래서
                        여기 적힌 숫자를 더하면 실제로 원장 총액이 된다. 예전 문구는 구멍1을
                        «부분집합 제한»이라는 말로만 가리켰고 구멍2는 아예 언급이 없었다. */}
                    {/* ★★`included`면 두 통이 **이미 위 손익에서 빠졌다** — 그때도 그것들을
                        「손익 밖에 남은 합」에 세면 같은 돈을 두 번 세는 것이고, 사다리의
                        «−» 줄과 각주가 서로를 부정한다(적대 리뷰 P1-2. 투영 실측: 원장
                        797,431 = 손익 797,431인데 각주가 "손익 밖 99,232원"이라 말했다).
                        그래서 열거와 «남은 합»을 **같은 술어**로 가른다. */}
                    {p.ad_spend != null && (
                      <> 옵션 광고 원장(Billboard) <b>{won(p.ad_option_total)}</b>은
                        {" "}{outOfRange > 0 ? "다섯" : "네"} 통으로
                        갈라집니다 — 귀속분 <b>{won(p.ad_attributed)}</b>
                        {Number(p.ad_folded_deducted) > 0 && (
                          <> + 구조적 광고비 중 손익에 실린 몫 {won(p.ad_folded_deducted)}
                            {foldPct !== null && foldPct < 100 && <> ({foldPct}%)</>}</>
                        )}
                        {adUncosted > 0 && <> + 원가 미상 옵션 {won(p.ad_uncosted)}</>}
                        {noSalesDays > 0 && <> + 팔린 옵션의 판매 없는 날 {won(p.ad_no_sales_days)}</>}
                        {noSales > 0 && <> + 판매행 없는 옵션 {won(p.ad_no_sales)}</>}
                        {outOfRange > 0 && <> + 판매분석이 없는 기간 {won(p.ad_out_of_range)}</>}
                        {" "}(구조적 두 통에서 위의 «실린 몫»을 뺀 나머지
                        {outOfRange > 0 && <> — 「판매분석이 없는 기간」은 차감 대상이 아닙니다</>}).{" "}
                        {outsidePnl > 0
                          ? <>손익 밖에 남은 합이 <b>{won(String(outsidePnl))}</b>입니다
                              {/* ★술어가 `!partial`이면 안 된다(2R 트리아지): 남은 돈에 구멍1이
                                  섞여 있으면 「귀속할 판매가 없는 돈만」이 거짓이다. 판별자는
                                  basis가 아니라 **구멍1이 0인가**다 — 그게 그 문장의 내용이다. */}
                              {adUncosted === 0
                                ? " (원가 결손은 없고, 귀속할 판매가 없는 돈만 남았습니다)"
                                : ` (그중 ${won(p.ad_uncosted)}은 원가 미상이라 빠진 것이고, 아래 목록을 해소하면 손익에 들어옵니다)`}.</>
                          : <b>손익 밖에 남은 광고비는 없습니다.</b>}
                        {/* ★단정을 뺐다(2026-08-10 라이브 실측). 종전 문구는 이 차를 통째로
                            「Billboard(PA)와의 정의 차이입니다(수집 결손이 아닙니다)」라고 단정했는데,
                            창 07-11~08-09 원시 대조가 그걸 뒤집는다:
                              · 원시 coupang_ad_report 합 = 19,925,049 (= 이 화면의 계정 확정액)
                              · 원시 coupang_ad_option_daily 합 = 19,929,002 (계정보다 **많다**,
                                매일 같은 방향 −78~−269원 ≈ 0.02% — 이 몫은 정의 차이가 맞다)
                              · 그런데 원장 축은 19,917,755로 원시보다 **11,247원 적고**, 그게
                                부호를 뒤집어 +7,294를 만든다. 그 11,247의 원인은 **미확인**이다.
                            금액은 작지만(0.037%) «설명됐다»고 말하면 안 되는 자리다. */}
                        {/* ★부호를 숫자로 보여주지 않고 **방향을 말로** 쓴다(2026-08-11 QA).
                            종전: "계정 확정 광고비는 19,057,661원이고 원장과의 차는 −7,467원입니다".
                            백엔드의 `diff`는 **원장 − 계정**인데(rocket_1p_revenue.py:918) 문장이
                            «계정»을 먼저 놓아, 읽는 사람은 계정 − 원장(=+7,467)을 기대한다.
                            그래서 같은 사실이 **반대 방향으로 읽힌다.** 금액이 작아 손익엔 영향이
                            없지만, 이 화면의 존재 이유가 «숫자를 믿게 하는 것»이라 부호가
                            거짓말하면 안 된다. 절댓값 + 「많다/적다」로 방향을 못 박는다. */}
                        {" "}계정 확정 광고비(report/SALES)는 {won(p.ad_account_total)}이고,
                        {" "}<b>원장은 그보다 {won(Math.abs(Number(data.ad_reconciliation.diff)))}
                        {" "}{Number(data.ad_reconciliation.diff) < 0 ? "적습니다" : "많습니다"}</b>
                        {" "}— 대부분은 Billboard(PA)와의 <b>정의 차이</b>(분할 반올림)로 설명되지만,
                        {" "}<b>전액이 설명되지는 않았습니다.</b> 손익에는 원장 축을 씁니다.</>
                    )}
                  </p>
                </>
              );
            })()}
          </Card>

          {/* ── 일별 손익 (Jino 2026-08-07: "우리의 일일 손익을 보고 싶은거야") ──
              ★일별 순이익의 합 = 옵션별 표의 합. 둘 다 같은 「날짜×옵션」 원자를 다른 방향으로
                접은 것이라 원 단위까지 같다.
              ★★**사다리 타일과는 `basis='full'`일 때 다르다**(2026-08-10 적대 리뷰 P2):
                타일은 귀속 불가 광고비 두 통을 세후로 **추가 차감**하는데 그 차감은 날짜에
                배분되지 않는다(배분하면 그 날 행의 산술이 깨진다). 라이브 실측 창 08-04~08-10에서
                Σ일별 1,816,394.64 vs 타일 1,295,207.37 — 차 521,187.27원 = ad_folded×100/110.
                종전 주석은 「셋 다 원 단위까지 같다」였고 그건 이 분기에서 거짓이다.
                아래 각주가 그 차이를 금액으로 말한다 — 안 적으면 사용자가 합을 맞춰 보다 놀란다. */}
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
                    {/* ★「전량」이 아니다(2026-08-10 라이브 실측). `ad_spend_all`은 그날 **판매행이
                        있는 옵션**의 광고비만 누적한다(rocket_1p_revenue.py:501-503 — 판매 원자
                        루프 안이다). 창 08-03~08-09 실측: 일별 합 5,412,823 vs 옵션 원장 6,092,627,
                        차이 679,804원(11.2%)이 정확히 ad_no_sales + ad_no_sales_days다.
                        열 이름이 「전량」이면 그 11%가 없는 돈이 된다. */}
                    <Th right>광고비(판매행)</Th>
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
                스스로 맞습니다. 왼쪽의 「우리 매출(전량)」은 그날 전부이지만,
                {" "}<b>「광고비(판매행)」은 그날 전부가 아닙니다</b> — 그날 <b>판매행이 있는 옵션</b>의
                광고비만입니다. 판매행이 없던 옵션의 광고비(위 사다리의 「구멍2·구멍3」)는
                여기에 <b>안 들어갑니다</b>. 그래서 일별 광고비를 다 더해도 위 광고 원장 총액에
                {" "}<b>못 미칩니다</b> — 그 차이가 구멍2＋구멍3입니다.
                {/* ★「작습니다」라는 낱말을 일부러 피했다: 같은 블록의 아래 각주가 «일별 순이익의
                    합이 사다리보다 크다»를 말하는데, 기존 가드가 그 방향 뒤집힘을
                    `not.toContain("작습니다")`로 잡는다(rocket1pRevenueAdLedger.test.tsx).
                    가드를 느슨하게 하지 않고 이쪽 낱말을 바꾼다 — 가드가 더 중요하다. */}
                {" "}<b>세 열의 분모가 서로 다릅니다.</b> 이익률은 오른쪽 기준이니, 커버리지가
                100%가 아닌 날의 이익률을 전체에 적용하면 안 됩니다.
                <br />
                <b>1P는 판매수수료·배송비가 없습니다</b>(쿠팡 부담) — 그래서 그 열이 없습니다.
                차감되는 비용은 원가·프로모션 분담금·광고비·납부세액 넷뿐입니다.
                {/* ★제목·인과절이 **두 분기 모두에서 참**이라야 한다(2026-08-10 적대 리뷰 P1).
                    첫 판은 「날짜에 못 붙는」이라 썼는데 그건 거짓이다 — 세 통 전부 `daily[]`에
                    날짜별 값이 있고(바로 아래 표시조건이 그걸 읽는다), 못 싣는 진짜 이유는
                    **이 열이 «원가 확인분» 축이라 더하면 그 행의 산술이 깨지는 것**이다.
                    각주의 거짓말을 없애려던 수정이 같은 문장에 새 거짓말을 넣었었다. */}
                {data.daily.some((d) => Number(d.ad_no_sales) + Number(d.ad_no_sales_days)
                  + Number(d.ad_uncosted) > 0) && (
                  /* ★2026-08-11(D-CPP-38): 통이 5개가 되면서 이 등식이 12,969,126원 어긋났다
                     (적대 리뷰 P1-1 — 좌변이 우변의 98배로 인쇄됐다). 통을 늘릴 때마다 이
                     문장을 고쳐야 하는 구조였던 게 원인이라, **열거를 조건부로** 만든다. */
                  <> 일별 광고비 열엔 <b>{Number(data.pnl.ad_out_of_range ?? 0) > 0
                      ? "네 통이" : "세 통이"} 빠져 있습니다</b>
                    (기간 합 {won(data.pnl.ad_unattributed)} = 원가 미상 {won(data.pnl.ad_uncosted)}
                    {" "}+ 팔린 옵션의 판매 없는 날 {won(data.pnl.ad_no_sales_days)}
                    {" "}+ 판매행 없는 옵션 {won(data.pnl.ad_no_sales)}
                    {Number(data.pnl.ad_out_of_range ?? 0) > 0 &&
                      <> + 판매분석이 없는 기간 {won(data.pnl.ad_out_of_range)}</>}
                    ). 이 열은 <b>원가 확인분 축</b>
                    이라 여기에 더하면 그 행의 산술이 깨집니다.{" "}
                    {/* ★★`included`면 **뒤 두 통만** 기간 사다리에 이미 들어가 있다. `ad_uncosted`는
                        어느 분기에서도 차감되지 않으므로 «이 셋은»으로 묶어 말하면 거짓이 된다
                        (적대 리뷰 P2 — 엔진에서 그 조합은 오늘 안 나오지만 화면은 타입이 허용하는
                        모든 응답에서 옳아야 한다). */}
                    {/* ★false 분기도 **원인을 하나로 뭉뚱그리지 않는다**(2R 이월): 「귀속할 판매가
                        없어」는 뒤 두 통에만 맞고 `ad_uncosted`엔 틀리다 — 그 옵션은 팔렸고 원가가
                        미상이라 빠진 것이다. 세 통의 사유가 각각 다르므로 여기선 **사실만** 말하고
                        사유는 각 사다리 줄의 sub가 말한다. */}
                    {/* ★술어를 불리언에서 **실제 차감액**으로 바꾼다 — 비율 차감이라 「전부
                        들어갔다/하나도 안 들어갔다」 둘 다 대개 거짓이다(적대 리뷰 P1-1). */}
                    {Number(data.pnl.ad_folded_deducted) > 0
                      ? <>그중 <b>{won(data.pnl.ad_folded_deducted)}</b>은 구조적 광고비의
                        {data.pnl.ad_deduct_share != null
                          && Number(data.pnl.ad_deduct_share) < 1
                          && <> {Math.round(Number(data.pnl.ad_deduct_share) * 1000) / 10}% 몫으로</>}
                        {" "}<b>기간 사다리 순이익에 이미 차감돼 있습니다</b> — 그래서
                        <b> 일별 순이익의 합이 위 사다리보다 {won(
                          Number(data.pnl.ad_folded_deducted) * 100 / 110)} 큽니다</b>
                        (그 차감은 날짜에 배분되지 않습니다).</>
                      : <>전부 <b>위 손익에도 안 들어갔습니다</b> — 실제로 나간 돈입니다
                        (사유는 위 사다리의 각 줄에 적혀 있습니다).</>}</>
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
