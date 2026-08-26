// OtaoPurchaseOrders.tsx — 「발주(OTAO)」. SKU별 발주 누계 · 픽업 누계 · 예약 잔량 3칸.
//
// 계약 `docs/contracts/CONTRACT_inventory_unified.md` §4 **S1**의 표면이다. 합격기준 원문:
//   *"콘솔 발주 메뉴(신설)에서 Jino가 SKU별 발주 누계 · 픽업 누계 · OTAO 예약 잔량을
//     3칸으로 나뉘어 본다."*
//
// ★★**세 칸을 합치지 않는다** — 계약 §3-9 금지선이다. *"합산하면 ②픽업 결정이 화면에서
//   사라진다."* 우리 결정변수는 둘이고 목적함수가 다르다:
//     ①발주층 — OTAO에 생산을 예약한다. 목적함수 = 기대 데드스톡 채무 + 기대 생산품절 손실
//     ②픽업층 — 예약 잔량 «상한 안에서» 언제·얼마를 가져올지. Jino의 「재고 최소화」가 여기 산다
//   그래서 「총 몇 개」는 어느 결정에도 답을 못 준다. 3칸이라야 n=2 실측이 화면에서 보인다:
//   *"창고엔 7개인데 OTAO엔 3,150개가 이미 우리 것으로 잡혀 있고, 그 상태에서 300개를 또 시켰다."*
//
// ★이 화면이 **자백해야 하는 3가지** (계약 §2-8·§2-9 · roster.py docstring):
//   ① 데이터 구간 — 잔량은 통관 원장이 덮는 창(2026-01-27~) «안»의 발주분만 센 값이다.
//      그 이전 발주는 입고가 원장에 없어 잔량이 부풀려지므로 **따로 칸을 둔다.**
//   ② 매핑 필요 — 픽업 칸에 못 붙은 원장 품목명(실측 12.8%). 숨기면 그만큼이 조용한 발주 누락.
//   ③ 음수 잔량 — 「창이 어긋났다」는 신호다. 0으로 깎으면 그 신호가 사라진다.
//
// ★그리고 **「원장이 비었다」와 「발주가 0이다」를 가른다.** 적재(`scripts/otao_po_import.py`)를
//   안 돌린 상태에서 0을 그리면 화면이 거짓말을 한다 — n=4가 정확히 그 상태로 닫혔다.
//
// ★읽기 전용이다. 이 화면에 발주를 «보내는» 버튼은 없다(계약 §3-2 「자동 실행 금지」).
import { Card, Table, Th, Td, Badge, Loading, EmptyState } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { num } from "../lib/format";
import { fetchOtaoRoster, fetchOtaoSales, fetchOtaoSettlement } from "../lib/api";
import OtaoSalesPanel from "./otaoSalesPanel";
import OtaoSettlementPanel from "./otaoSettlementPanel";

/** 예약 잔량 셀 — 음수를 **음수로** 그린다. 깎으면 창 어긋남 신호가 사라진다(자백 ③). */
function ReservedCell({ value }: { value: number }) {
  if (value < 0) {
    return (
      <span
        className="text-amber-700 font-medium"
        title="음수입니다 — 창 밖(2026-01-27 이전) 발주분의 입고가 창 안에 찍혔다는 신호입니다. 0으로 깎지 않았습니다."
      >
        {num(value)} ⚠
      </span>
    );
  }
  return <span className="font-medium">{num(value)}</span>;
}

export default function OtaoPurchaseOrders() {
  const { data, error } = useAsyncData(() => fetchOtaoRoster(), []);
  // ★판매 축(S3)은 **따로** 가져온다. 로스터가 비어 있어도 판매는 보여야 하고, 반대도 같다 —
  //   한쪽 실패가 다른 쪽을 통째로 지우면 화면이 「없다」고 거짓말한다.
  const { data: sales, error: salesError } = useAsyncData(() => fetchOtaoSales(60), []);
  // ★정산 축(S2)도 **따로** 가져온다. 원천이 통관 원장이라 «발주» 원장이 비어 있어도 살아 있다 —
  //   위 `ledger_empty`는 발주서 적재 여부이지 픽업 여부가 아니다. 묶으면 화면이 「픽업도 없다」로
  //   거짓말한다(판매 축과 같은 이유).
  const { data: settlement, error: settlementError } = useAsyncData(
    () => fetchOtaoSettlement(),
    [],
  );

  if (error) {
    return (
      <div className="p-6">
        <EmptyState reason={`발주 로스터를 불러오지 못했습니다: ${error}`} />
      </div>
    );
  }
  if (!data) return <Loading label="발주 로스터를 불러오는 중…" rows={6} />;

  // ★정산 축(S2) 섹션. 계약 §4 S2의 표면이다 — 「전월 20~당월 19에 픽업한 «금액»」이 OTAO에
  //   지급할 돈이고, 위 3칸의 「픽업 누계」는 수량이라 다른 질문에 답한다.
  const settlementSection = (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between border-t border-gray-200 pt-5">
        <h2 className="text-base font-semibold text-gray-900">정산 창 (OTAO 지급)</h2>
        <p className="text-xs text-gray-500">
          창은 전월 20일 ~ 당월 19일이고 그 달 19일에 지급합니다 — 20일 픽업부터는 다음 창입니다.
        </p>
      </div>
      {settlementError ? (
        <EmptyState reason={`정산 창을 불러오지 못했습니다: ${settlementError}`} />
      ) : !settlement ? (
        <Loading label="정산 창을 불러오는 중…" rows={4} />
      ) : (
        <OtaoSettlementPanel data={settlement} />
      )}
    </section>
  );

  // ★판매 축(S3) 섹션. 발주 원장이 비어 있어도 이건 보여야 한다 — 두 축은 원천이 다르다.
  const salesSection = (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between border-t border-gray-200 pt-5">
        <h2 className="text-base font-semibold text-gray-900">판매 (채널 통합)</h2>
        <p className="text-xs text-gray-500">
          축은 상품 SKU(OHI-…)입니다 — 위 발주 표의 상품코드(GAPIP…)와 **다른 축**이라 같은 줄로
          읽으면 안 됩니다.
        </p>
      </div>
      {salesError ? (
        <EmptyState reason={`판매 시계열을 불러오지 못했습니다: ${salesError}`} />
      ) : !sales ? (
        <Loading label="판매 시계열을 불러오는 중…" rows={4} />
      ) : (
        <OtaoSalesPanel data={sales} />
      )}
    </section>
  );

  // ★자백 ⓪ — 「적재를 안 돌렸다」를 0으로 그리지 않는다.
  if (data.ledger_empty) {
    return (
      <div className="p-6 space-y-4">
        <h1 className="text-lg font-semibold text-gray-900">발주 (OTAO)</h1>
        <Card title="발주 원장이 비어 있습니다">
          <EmptyState
            reason="발주서가 아직 한 건도 적재되지 않았습니다 — 「발주 0건」이 아니라 «아직 안 심었다»는 뜻입니다."
            hint="발주서 PDF는 Google Drive에 있고 서버는 그 폴더를 못 봅니다. Mac에서 scripts/otao_po_export.py 로 페이로드를 만들고, 서버에서 scripts/otao_po_import.py 로 넣습니다."
          />
        </Card>
        {/* ★발주가 비어도 정산·판매 축은 살아 있다 — 여기서 지우면 「픽업·판매도 없다」로 읽힌다. */}
        {settlementSection}
        {salesSection}
      </div>
    );
  }

  const t = data.totals;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold text-gray-900">발주 (OTAO)</h1>
        <p className="text-xs text-gray-500">
          정본 발주서 {num(data.source.orders_authoritative)}건
          {data.source.orders_superseded > 0 && (
            <span title="같은 발주번호의 개정 전 판본·중복 사본. 버리지 않고 보관하되 집계에서 뺍니다(D-INV-3).">
              {" "}(대체됨 {num(data.source.orders_superseded)}건)
            </span>
          )}
          {data.source.last_order_date && <> · 최근 발주 {data.source.last_order_date}</>}
        </p>
      </div>

      {/* ★자백 ①②③ — 백엔드가 준 문장을 그대로 싣는다. 화면이 스스로 지어내지 않는다. */}
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
        title="SKU별 3칸"
        right={
          <span className="text-xs text-gray-400">
            합계 — 발주 {num(t.ordered)} · 픽업 {num(t.picked)} · 잔량 {num(t.reserved)}
          </span>
        }
      >
        {/* ★합계도 «세 칸»으로만 낸다. 합산 단일 숫자를 만들지 않는다(§3-9). */}
        <Table
          head={
            <>
              <Th>상품코드</Th>
              <Th right>발주 누계</Th>
              <Th right>픽업 누계</Th>
              <Th right>예약 잔량</Th>
              <Th right>창 밖 발주</Th>
              <Th right>발주 건수</Th>
              <Th>최근 발주일</Th>
            </>
          }
        >
          {data.rows.map((r) => (
            <tr key={r.product_code}>
              <Td>{r.product_code}</Td>
              <Td right>{num(r.ordered)}</Td>
              <Td right>{num(r.picked)}</Td>
              <Td right><ReservedCell value={r.reserved} /></Td>
              <Td right>
                {r.out_of_window_ordered > 0 ? (
                  <span
                    className="text-gray-500"
                    title={`통관 원장이 덮기 시작한 ${data.window_start ?? "—"} 이전 발주분입니다. 입고가 원장에 없어 잔량 계산에서 뺐습니다.`}
                  >
                    {num(r.out_of_window_ordered)}
                  </span>
                ) : (
                  <span className="text-gray-300">0</span>
                )}
              </Td>
              <Td right>{num(r.order_count)}</Td>
              <Td>{r.last_order_date ?? <span className="text-gray-300">—</span>}</Td>
            </tr>
          ))}
        </Table>
      </Card>

      {/* ★자백 ② — 못 붙인 품목명을 «수량과 함께» 보인다. 조용히 빼면 발주 누락이 된다. */}
      <Card
        title="매핑 필요 — 픽업 누계에서 빠져 있는 품목명"
        right={
          <Badge tone={data.unmapped.length > 0 ? "alert" : "good"}>
            {num(data.source.name_map_resolved)}/{num(data.source.name_map_total)} 붙음
          </Badge>
        }
      >
        {data.unmapped.length === 0 ? (
          <EmptyState reason="통관 원장의 모든 품목명이 상품코드에 붙었습니다 — 픽업 누계에서 빠진 수량이 없습니다." />
        ) : (
          <Table
            head={
              <>
                <Th>원장 품목명</Th>
                <Th right>수량</Th>
              </>
            }
          >
            {data.unmapped.map((u) => (
              <tr key={u.item_name}>
                <Td>{u.item_name}</Td>
                <Td right>{num(u.quantity)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <p className="text-xs leading-relaxed text-gray-500">
        ★세 칸은 합치지 않습니다. 「발주할지」와 「가져올지」는 다른 결정이고, 합산하면 뒤의 결정이
        화면에서 사라집니다(계약 §3-9). 이 화면은 읽기 전용이며 발주를 보내지 않습니다.
      </p>

      {settlementSection}

      {salesSection}
    </div>
  );
}
