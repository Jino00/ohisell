// otaoSalesPanel.tsx — 「발주(OTAO)」 화면의 **판매 축** 섹션. 계약 §4 **S3**의 표면이다.
//
// 합격기준 원문:
//   *"같은 메뉴에서 **채널 통합 SKU별 판매수량 시계열**과 채널별 매핑률이 보이고,
//     **결손일이 「0」이 아니라 「데이터 없음」으로 구분 표시**된다."*
//
// ★★**이 표를 발주 3칸 표와 «같은 줄»에 놓지 않는다.** 두 표의 SKU 축이 다르기 때문이다 —
//   발주는 `product_code`(GAPIP…), 판매는 `internal_sku`(OHI-…)이고 prod에서 **0% 겹친다**.
//   나란히 놓으면 사람은 반드시 같은 줄로 읽는다. 그게 「말이 되는 것처럼 보이는 거짓 대비」다.
//   그래서 별도 카드로 두고, 다리가 없다는 사실 자체를 **배지로 먼저** 말한다.
//
// ★이 화면이 자백해야 하는 것 넷:
//   ① 채널별 **매핑률** — `null`은 «0%»가 아니라 «잴 수 없음»이다. 다르게 그린다.
//   ② **결손 구분 근거가 없는 채널** — 빈 날을 0으로도 결손으로도 칠하지 않는다(계약 §2-8).
//   ③ **취소·반품으로 뺀 몫** — 조용히 빼면 화면 숫자가 원장과 안 맞고 되짚을 수 없다.
//   ④ **못 붙은 판매 수량** — 숨기면 그만큼 수요가 사라진다(§2-9).
import { Card, Table, Th, Td, Badge, EmptyState } from "../components/ui";
import { num } from "../lib/format";
import type { OtaoSales, OtaoSalesChannel } from "../lib/api";

/** 매핑률 셀 — `null`(잴 수 없음)과 `0`(전부 실패)은 **다른 상태**라 다르게 그린다. */
export function MappingRateCell({ channel }: { channel: OtaoSalesChannel }) {
  if (channel.mapping_rate === null) {
    return (
      <span className="text-gray-400" title="이 창에 판매 수량이 없어 매핑률을 잴 수 없습니다 — 0%가 아닙니다.">
        잴 수 없음
      </span>
    );
  }
  const tone = channel.mapping_rate >= 95 ? "good" : channel.mapping_rate >= 70 ? "neutral" : "alert";
  return <Badge tone={tone}>{channel.mapping_rate}%</Badge>;
}

/** 결손일 셀 — 근거가 없으면 «구분 불가»라고 말한다. 지어내지 않는다(자백 ②). */
export function MissingDayCell({ channel }: { channel: OtaoSalesChannel }) {
  if (!channel.missing_day_evidence) {
    return (
      <span
        className="text-gray-500"
        title={`이 채널의 원천 테이블(${channel.source_table})은 수집 로그가 덮지 않습니다. 그래서 빈 날이 「판매 0」인지 「수집 안 됨」인지 구분할 근거가 없습니다 — 0으로도 결손으로도 단정하지 않습니다.`}
      >
        구분 근거 없음
      </span>
    );
  }
  return (
    <span>
      <span className="text-gray-600" title="수집 성공 run이 그 날짜를 덮었는데 판매가 없던 날입니다 — 진짜 「판매 0」입니다.">
        판매 0 {num(channel.days_collected_zero.length)}일
      </span>
      {channel.days_no_data.length > 0 && (
        <>
          {" · "}
          <span
            className="text-amber-700 font-medium"
            title={`수집 성공 run이 덮지 않은 날짜입니다 — 「데이터 없음」이지 「판매 0」이 아닙니다. ${channel.days_no_data.slice(0, 10).join(", ")}${channel.days_no_data.length > 10 ? " 외" : ""}`}
          >
            데이터 없음 {num(channel.days_no_data.length)}일 ⚠
          </span>
        </>
      )}
    </span>
  );
}

/**
 * SKU 한 줄의 **일별 시계열**을 그린다 (적대 리뷰 P1-2).
 *
 * ★S3 원문의 첫 요구가 「시계열」이다. 창 합계만 그리면 «언제 팔렸나»가 사라지고, 그건
 * 발주 판단에서 가장 중요한 축이다(같은 60개라도 최근 3일에 몰린 것과 두 달에 퍼진 것은
 * 다른 결정을 부른다). 외부 차트 라이브러리 없이 인라인 SVG로 그린다.
 */
export function Sparkline({ series, dates }: { series: number[]; dates: string[] }) {
  const max = Math.max(...series, 1);
  const w = 120;
  const h = 20;
  const step = series.length > 1 ? w / (series.length - 1) : w;
  const pts = series
    .map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`)
    .join(" ");
  const peak = series.indexOf(max);
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="inline-block align-middle"
      role="img"
      aria-label={`일별 판매 추이 — 최대 ${max}개 (${dates[peak] ?? "—"})`}
    >
      <title>{`${dates[0] ?? ""} ~ ${dates[dates.length - 1] ?? ""} · 최대 ${max}개 (${dates[peak] ?? "—"})`}</title>
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.2" className="text-sky-600" />
    </svg>
  );
}

export default function OtaoSalesPanel({ data }: { data: OtaoSales }) {
  const byKey = new Map(data.channels.map((c) => [c.key, c]));
  const active = data.channels.filter((c) => c.quantity > 0);
  const unmappedTotal = data.unmapped.reduce((a, u) => a + u.quantity, 0);
  const bridgeMissing = data.order_axis.overlap === 0;

  return (
    <div className="space-y-4">
      {/* ★자백 — 백엔드가 준 문장을 그대로 싣는다. 화면이 지어내지 않는다. */}
      {data.notes.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <ul className="space-y-1">
            {data.notes.map((n) => (
              <li key={n} className="text-xs leading-relaxed text-amber-900 whitespace-pre-line">
                {n}
              </li>
            ))}
          </ul>
        </div>
      )}

      <Card
        title="채널별 판매 — 매핑률과 결손일"
        right={
          <span className="text-xs text-gray-400">
            {data.window_start} ~ {data.window_end} ({num(data.days)}일)
          </span>
        }
      >
        <Table
          head={
            <>
              <Th>채널</Th>
              <Th>법인</Th>
              <Th right>판매수량</Th>
              <Th right>매핑됨</Th>
              <Th>매핑률</Th>
              <Th right>취소·반품</Th>
              <Th right>매핑 모호</Th>
              <Th right>데이터 있는 날</Th>
              <Th>빈 날의 정체</Th>
            </>
          }
        >
          {data.channels.map((c) => (
            <tr key={c.key}>
              <Td>
                <span title={`원천 ${c.source_table} · 다리 ${c.bridge}`}>{c.label}</span>
              </Td>
              <Td><span className="text-xs text-gray-500">{c.company}</span></Td>
              <Td right>{num(c.quantity)}</Td>
              <Td right>{num(c.quantity_mapped)}</Td>
              <Td><MappingRateCell channel={c} /></Td>
              <Td right>
                {/* ★자백 ③ — 뺀 몫을 보인다. 0이어도 칸을 비우지 않는다. */}
                {c.quantity_excluded > 0 ? (
                  <span className="text-gray-500" title="취소·반품은 수요가 아니라 판매수량에서 뺐습니다. 뺀 몫을 여기 남깁니다.">
                    −{num(c.quantity_excluded)}
                  </span>
                ) : (
                  <span className="text-gray-300">0</span>
                )}
              </Td>
              <Td right>
                {/* ★한 채널 상품 ID가 서로 다른 상품 여럿을 가리켜 «안 붙인» 몫. 다수결로
                    고르면 그만큼이 조용한 발주 오염이 된다 — 그래서 숫자로 드러낸다. */}
                {c.quantity_ambiguous > 0 ? (
                  <span
                    className="text-amber-700 font-medium"
                    title="이 채널의 상품 ID 하나가 서로 다른 상품 여러 개에 매핑돼 있어 어느 상품의 판매인지 정할 수 없는 수량입니다. 다수결로 고르지 않고 남겨 둡니다."
                  >
                    {num(c.quantity_ambiguous)} ⚠
                  </span>
                ) : (
                  <span className="text-gray-300">0</span>
                )}
              </Td>
              <Td right>{num(c.days_with_rows)}</Td>
              <Td><MissingDayCell channel={c} /></Td>
            </tr>
          ))}
        </Table>
      </Card>

      {/* ★자백 ④ — 못 붙은 판매 수량. 숨기면 그만큼 수요가 사라진다. */}
      {unmappedTotal > 0 && (
        <Card
          title="매핑 필요 — SKU 시계열에서 빠져 있는 판매"
          right={<Badge tone="alert">{num(unmappedTotal)}개</Badge>}
        >
          <Table head={<><Th>채널</Th><Th right>수량</Th></>}>
            {data.unmapped.map((u) => (
              <tr key={u.channel}>
                <Td>{byKey.get(u.channel)?.label ?? u.channel}</Td>
                <Td right>{num(u.quantity)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
      )}

      <Card
        title="SKU별 채널 통합 판매수량"
        right={<span className="text-xs text-gray-400">{num(data.rows.length)}개 SKU</span>}
      >
        {data.rows.length === 0 ? (
          <EmptyState reason="이 창에 상품코드에 붙은 판매가 없습니다." />
        ) : (
          <Table
            head={
              <>
                <Th>SKU</Th>
                <Th>상품명</Th>
                {/* ★S3 원문의 첫 요구 — 일별 추이. 창 합계만 그리면 «언제 팔렸나»가 사라진다. */}
                <Th>일별 추이 ({data.dates[0]} ~ {data.dates[data.dates.length - 1]})</Th>
                {active.map((c) => (
                  <Th key={c.key} right>{c.label}</Th>
                ))}
                <Th right>합계</Th>
              </>
            }
          >
            {data.rows.map((r) => (
              <tr key={r.internal_sku}>
                <Td>{r.internal_sku}</Td>
                <Td><span className="text-xs text-gray-600">{r.product_name ?? "—"}</span></Td>
                <Td><Sparkline series={r.series} dates={data.dates} /></Td>
                {active.map((c) => (
                  <Td key={c.key} right>
                    {r.by_channel[c.key] ? (
                      num(r.by_channel[c.key])
                    ) : (
                      <span className="text-gray-300">0</span>
                    )}
                  </Td>
                ))}
                <Td right><span className="font-medium">{num(r.total)}</span></Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {/* ★★가장 중요한 자백 — 두 축을 잇는 다리가 없다. 이걸 안 말하면 위 표가 발주 표와
          같은 줄로 읽힌다. */}
      <div
        className={`rounded-lg border px-4 py-3 ${
          bridgeMissing ? "border-amber-300 bg-amber-50" : "border-gray-200 bg-gray-50"
        }`}
      >
        <p className="text-xs leading-relaxed text-amber-900 whitespace-pre-line">
          <span className="font-medium">
            발주 축 ↔ 판매 축 다리: 겹치는 값 {num(data.order_axis.overlap)}개
          </span>
          {" — "}
          발주 축 코드 {num(data.order_axis.order_axis_codes)}종(GAPIP…) vs 판매 축 SKU{" "}
          {num(data.order_axis.sales_axis_skus)}종(OHI-…).
          {"\n"}
          {data.order_axis.note}
        </p>
      </div>
    </div>
  );
}
