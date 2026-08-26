// otaoSettlementPanel.tsx — 「발주(OTAO)」 화면의 **정산 창** 섹션. 계약 §4 **S2**의 표면이다.
//
// 합격기준 원문:
//   *"같은 메뉴에서 정산 창(전월 20~당월 19) **픽업 합계**가 보이고, **실제 19일 OTAO
//     지급액과 1개 창 이상 대조 일치**한다."*
//
// ★**금액 단위는 CNY다.** 우리가 OTAO에 주는 돈은 Commercial Invoice의 외화 금액이고,
//   과세금액(원)은 관세청이 세금을 매기는 값이라 OTAO는 그 숫자를 모른다. 실송금 환율이
//   원장에 없어(prod 12/12 NULL) 원화 환산도 하지 않는다 — 하면 우리가 안 쓰는 환율로
//   지어낸 숫자가 화면에 선다.
//
// ★이 화면이 자백해야 하는 것 넷:
//   ① **대조할 «대상»이 없다** — 실제 지급액 원장이 이 저장소에 없다(prod 123개 테이블 전수
//      검색 0건). 「대조 불가」와 「대조했는데 틀렸다」는 다른 상태이고, 후자로 그리면
//      화면이 없는 사실을 말한다. 그래서 `reconciled === null`을 **따로** 그린다.
//   ② **부자재가 갈라져 있는 것** — 부자재(cleaning kits)는 지급액엔 들어가고 위 발주 표의
//      «픽업 누계» 칸엔 안 들어간다. 합쳐 놓으면 두 숫자가 왜 다른지 아무도 설명 못 한다.
//   ③ **창 경계 ±2일 선적** — 이 원장엔 OTAO 픽업일이 없고 한국 쪽 신고일만 있다. 경계 근처
//      선적은 실제 정산 창이 한 칸 밀려 있을 수 있고, 대조가 어긋나면 첫 번째 후보다.
//   ④ **draft 선적이 합계에 들어 있는 것** — 빼면 픽업이 축소되고, 말없이 넣으면 확정된 창과
//      구별이 사라진다.
import { Card, Table, Th, Td, Badge, EmptyState } from "../components/ui";
import { num } from "../lib/format";
import type { OtaoSettlement, OtaoSettlementWindow } from "../lib/api";

/** 금액 셀 — 0과 «없음»을 가른다. `null`은 「모른다」이지 「0원」이 아니다(`num`이 —를 준다). */
function cny(v: number | null | undefined): string {
  return num(v == null ? null : Math.round(v));
}

/**
 * 대조 셀 — **세 상태를 세 가지로** 그린다 (자백 ①).
 *
 * `null`을 「불일치」로 접거나 「일치」로 접으면 둘 다 거짓말이다. 지금 prod는 전 창이
 * `null`이고, 그게 정직한 값이다 — 지급액 원장이 없기 때문이다.
 */
export function ReconciledCell({ w }: { w: OtaoSettlementWindow }) {
  if (w.reconciled === null) {
    return (
      <span
        className="text-gray-500"
        title="실제 OTAO 지급액을 담은 원장이 이 저장소에 없어 대조할 대상이 없습니다 — 「불일치」가 아니라 「대조 불가」입니다. 창별 지급액을 주시면 그 자리에서 대조됩니다."
      >
        대조 불가
      </span>
    );
  }
  if (w.reconciled) return <Badge tone="good">일치</Badge>;
  return (
    <span className="text-amber-700 font-medium" title="픽업 합계와 실제 지급액이 다릅니다. 창 경계 ±2일 선적(픽업일 축 부재)과 draft 선적이 첫 번째 후보입니다.">
      차액 {cny(w.difference_cny)} ⚠
    </span>
  );
}

/** 선적 수 셀 — draft·경계 선적을 숫자 옆에서 바로 자백한다 (자백 ③④). */
export function ShipmentCountCell({ w }: { w: OtaoSettlementWindow }) {
  const marks: string[] = [];
  if (w.draft_shipment_ids.length) marks.push(`미확정 ${w.draft_shipment_ids.length}건`);
  if (w.boundary_shipment_ids.length) marks.push(`창 경계 ${w.boundary_shipment_ids.length}건`);
  return (
    <span>
      {num(w.shipments)}
      {marks.length > 0 && (
        <span
          className="ml-1 text-amber-700"
          title={
            (w.draft_shipment_ids.length
              ? "미확정(draft) 선적이 합계에 들어 있습니다 — 검산 3종을 아직 통과하지 못했습니다. 빼지도 숨기지도 않았습니다. "
              : "") +
            (w.boundary_shipment_ids.length
              ? "창 경계(19/20일) ±2일 안에 신고된 선적입니다. 이 원장엔 OTAO 픽업일이 없고 한국 쪽 신고일만 있어, 실제 정산 창이 한 칸 밀려 있을 수 있습니다."
              : "")
          }
        >
          ⚠ {marks.join(" · ")}
        </span>
      )}
    </span>
  );
}

export default function OtaoSettlementPanel({ data }: { data: OtaoSettlement }) {
  // ★「원장이 비었다」와 「픽업이 0이었다」는 다른 상태다. 0을 그리면 화면이 거짓말한다.
  if (data.ledger_empty) {
    return (
      <EmptyState reason="통관 원장이 비어 있어 정산 창을 만들 근거가 없습니다 — 「픽업 0」이 아니라 「데이터 없음」입니다." />
    );
  }

  const t = data.totals;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="neutral">
          창 {num(data.windows.length)}개 · {data.ledger_start} ~ {data.ledger_end}
        </Badge>
        <Badge tone="neutral">단위 {data.currency}</Badge>
        {/* ★자백 ① — 대조 상태를 표 «앞»에서 먼저 말한다. 표 안에만 있으면 안 읽힌다. */}
        {data.reconciliation.source === "none" ? (
          <Badge tone="alert">지급액 대조 불가 — 실제 지급액 원장 없음</Badge>
        ) : (
          <Badge tone={data.reconciliation.windows_matched > 0 ? "good" : "alert"}>
            대조 {num(data.reconciliation.windows_matched)}/{num(data.reconciliation.windows_compared)} 창 일치
          </Badge>
        )}
      </div>

      <Table
        head={
          <>
            <Th>정산 창</Th>
            <Th>기간 (지급일)</Th>
            <Th right>선적</Th>
            <Th right>상품 수량</Th>
            <Th right>상품 {data.currency}</Th>
            {/* ★자백 ② — 부자재를 상품과 «같은 칸»에 넣지 않는다. */}
            <Th right>부자재 수량</Th>
            <Th right>부자재 {data.currency}</Th>
            <Th right>픽업 합계 {data.currency}</Th>
            <Th right>실제 지급액</Th>
            <Th>대조</Th>
          </>
        }
      >
        {data.windows.map((w) => (
          <tr key={w.key}>
            <Td>
              <span className="font-medium">{w.key}</span>
            </Td>
            <Td>
              <span className="text-xs text-gray-500">
                {w.start} ~ {w.end}
              </span>
            </Td>
            <Td right>
              <ShipmentCountCell w={w} />
            </Td>
            <Td right>{num(w.product_quantity)}</Td>
            <Td right>{cny(w.product_amount_cny)}</Td>
            <Td right>
              {w.material_quantity ? num(w.material_quantity) : <span className="text-gray-300">0</span>}
            </Td>
            <Td right>
              {w.material_amount_cny ? cny(w.material_amount_cny) : <span className="text-gray-300">0</span>}
            </Td>
            <Td right>
              <span className="font-medium">{cny(w.total_amount_cny)}</span>
            </Td>
            {/* ★null을 0으로 그리지 않는다 — 「0원 지급」이 아니라 「모른다」다. */}
            <Td right>{cny(w.payment_actual_cny)}</Td>
            <Td>
              <ReconciledCell w={w} />
            </Td>
          </tr>
        ))}
        <tr className="bg-gray-50">
          <Td>
            <span className="font-semibold">합계</span>
          </Td>
          <Td>{null}</Td>
          <Td right>{num(Number(t.shipments ?? 0))}</Td>
          <Td right>{num(Number(t.product_quantity ?? 0))}</Td>
          <Td right>{cny(t.product_amount_cny)}</Td>
          <Td right>{num(Number(t.material_quantity ?? 0))}</Td>
          <Td right>{cny(t.material_amount_cny)}</Td>
          <Td right>
            <span className="font-semibold">{cny(t.total_amount_cny)}</span>
          </Td>
          <Td>{null}</Td>
          <Td>{null}</Td>
        </tr>
      </Table>

      {/* ★신고일이 없어 창에 못 넣은 라인 — 0으로 덮지 않는다(계약 §2-8). */}
      {data.unassigned.lines > 0 && (
        <Card>
          <div className="text-sm text-amber-700">
            신고일이 없어 어느 창에도 넣지 못한 라인이 {num(data.unassigned.lines)}건
            ({cny(data.unassigned.amount_cny)} {data.currency}) 있습니다 — 0으로 덮지 않았습니다.
          </div>
        </Card>
      )}

      <ul className="space-y-1 text-xs text-gray-500">
        {data.notes.map((n) => (
          <li key={n}>· {n}</li>
        ))}
      </ul>
    </div>
  );
}
