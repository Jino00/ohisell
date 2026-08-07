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
import { fetchRocket1PRevenue, type Rocket1PRevenueOption, type Rocket1PPnl } from "../lib/api";

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
  const net = o.net_profit == null ? null : Number(o.net_profit);
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
      <Td right>
        <span className={net == null ? "" : net >= 0 ? "font-medium text-judge-good" : "font-medium text-judge-bad"}>
          {won(o.net_profit)}
        </span>
      </Td>
      <Td right>{pct(o.profit_rate)}</Td>
      <Td right>{ratio(o.roas)}</Td>
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

/** 손익이 안 나오는 이유를 **하나만** 고른다 — 화면이 "왜 —인지" 말하게 하려고. */
function pnlBlockedReason(p: Rocket1PPnl, salesCovered: boolean): string | null {
  if (p.net_profit != null) return null;
  if (!salesCovered) return "이 기간엔 쿠팡 판매분석 수집분이 없습니다 — 판매가 0이었던 게 아니라 관측 불가입니다.";
  if (!p.promo_burden_known)
    return "이 기간에 걸친 프로모션의 할인액 원천(제안서)이 아직 없습니다. 분담금을 0으로 놓으면 그만큼 이익이 부풀어 보이므로 손익을 내지 않습니다.";
  return "이 기간에 팔린 SKU 중 등록원가가 붙은 것이 하나도 없습니다 — 아래 목록의 SKU 원가를 SellC에 등록하면 손익이 나옵니다.";
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
        <h1 className="text-xl font-bold text-gray-900">로켓배송(1P) 매출·손익 — 소비자가 ∥ 납품가</h1>
        <p className="mt-1 text-sm text-gray-500">
          쿠팡이 고객에게 판 금액과 우리가 쿠팡에 판 금액을 나란히 보고, 우리 매출(납품가)에서
          비용을 빼 손익까지 봅니다. 조회 전용이며, 이 화면의 값은 종합조망 순이익에
          결합되지 않습니다.
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
            {/* ★"이게 무슨 데이터냐"에 화면이 먼저 답한다(Jino 2026-08-07). 하루치로 오해하기
                쉬운 자리다 — 원천·그레인·집계 방식·기간을 한 줄에 박는다. */}
            <p className="mx-4 mt-3 rounded bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-600">
              <b>{data.period.from} ~ {data.period.to}</b>({data.freshness.days_expected}일)
              <b> 기간 합계</b>입니다 — 하루치가 아닙니다.
              원천은 쿠팡 <b>판매분석의 옵션×일 실적</b>(고객에게 실제로 팔린 수량·금액)이고,
              그 기간의 모든 날·모든 옵션을 더한 값입니다. 「계산서 매출」만 다른 원천
              (세금계산서)이며, 광고비는 광고 계정 리포트입니다.
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
              data.pnl.cost_coverage == null ? undefined
                : Number(data.pnl.cost_coverage) >= 1
                  ? <Badge tone="neutral">원가 확인 100% · 기간 전체</Badge>
                  : <Badge tone="alert">원가 확인 {pct(data.pnl.cost_coverage)}분만</Badge>
            }
          >
            {(() => {
              const p = data.pnl;
              const blocked = pnlBlockedReason(p, data.coverage.sales_data_covered);
              const partial = p.basis === "costed_subset";
              return (
                <>
                  {blocked ? (
                    <div className="px-4 py-4">
                      <EmptyState reason="이 기간의 손익을 낼 수 없습니다." hint={blocked} />
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
                          sub="제안서 실측 · 분담률 100%" />
                        <PnlLine label="광고비" value={won(p.ad_spend)} sign="minus"
                          sub="옵션 그레인(Billboard)" />
                        <PnlLine label="부가세 (납부세액)" value={won(p.vat)} sign="minus"
                          sub="매출VAT − 매입세액공제" />
                        <PnlLine label="순이익" value={won(p.net_profit)} sign="eq" strong
                          sub={`이익률 ${pct(p.profit_rate)}`} />
                      </div>
                    </>
                  )}

                  {/* ★"—"로 끝내지 않는다 — 무엇을 등록하면 채워지는지 이름으로 말한다. */}
                  {p.uncosted.skus > 0 && (
                    <div className="border-t border-gray-100 px-4 py-3">
                      <div className="text-sm font-medium text-gray-800">
                        원가 미등록 SKU {p.uncosted.skus}개 · 우리 매출 {won(p.uncosted.our_revenue)}
                      </div>
                      <p className="mt-0.5 text-xs text-gray-500">
                        아래 SKU의 원가를 SellC에 등록하면 그만큼 커버리지가 오르고, 100%가 되면
                        위 손익이 <b>기간 전체</b>의 값이 됩니다.
                      </p>
                      <Table
                        head={<>
                          <Th>SKU / 상품</Th>
                          <Th right>판매량</Th>
                          <Th right>우리 매출(납품가)</Th>
                          <Th right>소비자 매출</Th>
                        </>}
                      >
                        {p.uncosted.top.map((u) => (
                          <tr key={u.sku_id ?? u.product_name} className="hover:bg-gray-50">
                            <Td>
                              <div className="max-w-md truncate text-gray-900"
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
                      {p.uncosted.skus > p.uncosted.top.length && (
                        <p className="mt-2 text-xs text-gray-400">
                          매출 상위 {p.uncosted.top.length}개만 표시 · 총 {p.uncosted.skus}개
                        </p>
                      )}
                    </div>
                  )}

                  <p className="mx-4 mb-4 mt-1 text-xs leading-relaxed text-gray-500">
                    순이익 = 우리 매출 − 원가 − 프로모션 분담금 − 광고비 − 납부세액.
                    <b> 소비자 매출(쿠팡가)은 손익에 들어가지 않습니다</b> — 그건 쿠팡의 매출이지
                    우리 돈이 아닙니다. 여기 광고비는 옵션 그레인(Billboard)이라 위 「매출 두 축」의
                    계정 총액과 정의가 달라 완전히 같지 않습니다.
                  </p>
                </>
              );
            })()}
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
                  <Th right>원가</Th>
                  <Th right>광고비</Th>
                  <Th right>순이익</Th>
                  <Th right>이익률</Th>
                  <Th right>RoAS</Th>
                </>}
              >
                {data.options.map((o) => <OptionRow key={o.option_id} o={o} />)}
              </Table>
            )}
            <p className="px-4 py-3 text-xs leading-relaxed text-gray-500">
              「우리 매출」이 <b>—</b> 인 옵션은 납품단가를 못 붙인 것이고, 「원가」가 <b>—</b> 인
              옵션은 SellC 등록원가를 못 붙인 것입니다 — <b>둘 다 0원이 아니라 «모름»</b>이라,
              그 옵션은 순이익도 내지 않습니다. 방문자·전환은 「유입·전환 퍼널」 화면에 있습니다.
              옵션 광고비는 Billboard(PA 기준)이라 계정 총액과 정의가 달라 합이 정확히 맞지
              않습니다 — 현재 차이 {won(data.ad_reconciliation.diff)}.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
