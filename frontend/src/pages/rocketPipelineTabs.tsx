// rocketPipelineTabs.tsx — `/rocket-recon`의 탭 둘 (계약 1P계산서 목표 S1·S2). 조회 전용.
//
// Jino 원문(2026-08-27): "1P의 경우 발행 후 계산서가 미발행된 내역을 sellC에서 보고 싶어" /
//   "거래명세서확인요청 내용을 SellC에서 모아서 볼 수 있나" / "이것까지 넣어서 종합적으로 보여줘".
//
// ★이 화면의 정직성 규칙 4개 — 숫자를 깔끔하게 만들려고 어기지 말 것:
//   1) **칸끼리 금액이 안 겹친다.** 소계는 ①②③뿐. ④지급대기는 «계산서» 그레인이라 더하면
//      같은 돈을 두 번 센다. RI(확인요청함)도 이미 ④에 들어 있어 파이프라인 합계에 없다.
//   2) **미해명은 확정 숫자가 아니다.** 발송 신고 > 쿠팡 인정 입고인 덩어리는 덜 보냄·반송·
//      진짜 미수금이 섞여 있다. 소계와 **다른 줄, 다른 색**으로 두고 「확정 아님」을 쓴다.
//      (2026-08-05에 이 값만 믿고 미수금을 5,763,290원 과대계상한 전례가 있다.)
//   3) **모르는 것을 아는 것처럼 보여주지 않는다.** 수집 창이 발주일 기준이라 오래된 미종결 PO는
//      상태가 마지막 수집일에 멈춘다. 칸마다 「오늘 수집분 / 지금 상태 모름」을 금액으로 가르고,
//      행에는 「YYYY-MM-DD 이후 다시 안 봄」 배지를 붙인다. RI 탭은 아예 섹션을 나눈다 —
//      실측(2026-08-27) RI 12건 중 8건이 마지막으로 본 지 3주 지난 상태였다.
//      ★문구 규칙(2026-08-28, Jino "표현을 굳음 말고 좀 더 명확하게"): 이 축의 사용자 문구에
//        **「확인」을 쓰지 않는다.** 이 화면에서 「확인」은 「거래명세서확인」 — 사람이 눌러
//        RI→CI로 보내는 동작이다. 조회 신선도에 같은 낱말을 쓰면(옛 「이후 미확인」·「마지막 확인」)
//        읽는 사람이 «아직 안 누른 건»으로 오해한다. 그리고 「굳음」 같은 상태 묘사 대신
//        **「모른다」는 사실**을 쓴다 — 요점은 데이터가 오래됐다는 게 아니라 지금 참인지 모른다는 것이다.
//   4) **보정·절단을 자백한다.** clamp(음수 절단)·ASN 미수집 보정·목록 잘림은 전부 화면에
//      한 줄로 쓴다. 조용히 처리하면 「깨끗한 화면」이 곧 「거짓말하는 화면」이 된다.
import { useEffect, useState } from "react";
import { Card, Table, Th, Td, Loading, EmptyState, Badge } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import {
  fetchRocketPipeline, fetchRocketPipelineStage, fetchRocketRiQueue, isPoStage,
  previewRocketInvoiceConfirm, requestRocketInvoiceConfirm, fetchRocketConfirmHistory,
  type RocketPipeline, type RocketPipelineRow, type RocketRiRow,
  type RocketConfirmPreview,
} from "../lib/api";

const NO_DATA = "—";

/** 금액은 Decimal → 문자열로 온다. 표시 직전에만 숫자로 바꾼다(정밀도 보존). */
function won(v: string | number | null | undefined): string {
  if (v == null) return NO_DATA;
  const num = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(num) ? `${Math.round(num).toLocaleString("ko-KR")}원` : NO_DATA;
}
function num(v: string | number | null | undefined): number {
  if (v == null) return 0;
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? n : 0;
}
const cnt = (v: number | null | undefined) => (v == null ? NO_DATA : v.toLocaleString("ko-KR"));

/** 칸 라벨·설명 — 백엔드는 key만 준다(표시 문구는 화면 몫). */
const STAGE_META: Record<string, { no: string; label: string; desc: string }> = {
  await_confirm: { no: "①", label: "확인 대기", desc: "발주가 왔고 우리가 아직 납품가능수량을 확정하지 않았다" },
  await_ship: { no: "②", label: "발송 대기", desc: "확정했고 아직 보내지 않았다" },
  await_receive: { no: "③", label: "입고 대기", desc: "보냈는데 쿠팡이 아직 안 잡았다 = 계산서 미발행" },
  await_payment: { no: "④", label: "지급 대기", desc: "계산서가 나갔고 지급일이 아직 오지 않았다" },
};

/** 「YYYY-MM-DD 이후 다시 안 봄」 배지. 신선도가 떨어진 행에만 붙는다.
 *
 *  ★문구에서 「확인」을 쓰지 않는다(2026-08-28, Jino 지시 "표현을 굳음 말고 좀 더 명확하게").
 *    이 화면에서 **「확인」은 이미 다른 뜻**이다 — 「거래명세서확인」, 즉 사람이 눌러 RI→CI로
 *    보내는 그 동작. 같은 낱말이 「우리가 조회했나」와 「사람이 눌렀나」 두 뜻으로 돌면
 *    배지를 읽는 사람이 «아직 안 누른 건»으로 오해한다. 그래서 「미확인」을 버렸다.
 *  ★그리고 「굳음」이라는 상태 묘사 대신 **「모른다」는 사실**을 말한다 — 이 표시의 요점은
 *    데이터가 오래됐다는 게 아니라 **지금 참인지 우리가 모른다**는 것이다. */
function StaleBadge({ since }: { since: string | null }) {
  return (
    <Badge tone="alert">{since ? `${since} 이후 다시 안 봄` : "마지막으로 본 날 모름"}</Badge>
  );
}

// ══════════════════════════════════════════════════════════════
// 탭 ①  「열린 파이프라인」
// ══════════════════════════════════════════════════════════════
export function PipelineTab() {
  // ★발송일 창은 **③에만** 걸린다. 기본은 «창 없음»(열려 있는 것 전부) — 기본 창을 두면
  //   창 밖에 굳어 있는 돈이 화면에서 사라지고, 그게 정확히 이 화면이 잡으려는 병이다.
  const [shipFrom, setShipFrom] = useState<string>("");
  const [shipTo, setShipTo] = useState<string>("");
  const [openStage, setOpenStage] = useState<string | null>(null);

  const { data, error } = useAsyncData(
    () => fetchRocketPipeline({ shipFrom: shipFrom || null, shipTo: shipTo || null }),
    [shipFrom, shipTo],
  );

  if (error) {
    return (
      <Card title="불러오지 못했습니다">
        <EmptyState
          reason={`파이프라인을 불러오지 못했습니다: ${error}`}
          hint="조회 실패는 '걸린 돈이 없다'와 다릅니다. 새로고침하거나 백엔드 상태를 확인하세요."
        />
      </Card>
    );
  }
  if (data === null) return <Card title="불러오는 중"><Loading rows={5} /></Card>;

  const clampLines: string[] = [];
  if (data.clamp.over_shipped.po_count > 0) {
    clampLines.push(
      `발송>확정 보정 ${data.clamp.over_shipped.po_count}건 · 합계 ${won(data.clamp.over_shipped.amount)}`,
    );
  }
  if (data.clamp.over_received.po_count > 0) {
    clampLines.push(
      `입고>발송 보정 ${data.clamp.over_received.po_count}건 · 합계 ${won(data.clamp.over_received.amount)}`,
    );
  }
  if (data.clamp.asn_missing.po_count > 0) {
    clampLines.push(
      `발송 기록 없이 입고된 발주 ${data.clamp.asn_missing.po_count}건 · 입고액 `
      + `${won(data.clamp.asn_missing.received_amount)} — 「안 보냄」이 아니라 「발송 기록 없음」이라 `
      + `입고액을 발송의 하한으로 썼습니다`,
    );
  }
  if (data.unknown_status.po_count > 0) {
    clampLines.push(
      `모르는 상태 코드(${data.unknown_status.codes.join(", ")})인 발주 ${data.unknown_status.po_count}건 · `
      + `확정액 ${won(data.unknown_status.confirmed_amount)} — 어느 칸에도 넣지 않았습니다`
      + `(「발송 대기」가 아니라 판정 불가입니다)`,
    );
  }
  if (data.unpriced_shipped_qty > 0) {
    clampLines.push(
      `단가를 못 붙인 발송수량 ${cnt(data.unpriced_shipped_qty)}개 — 금액에서 빠져 있습니다(0원으로 세지 않았습니다)`,
    );
  }

  return (
    <div className="space-y-4">
      <ShipWindowBar
        from={shipFrom} to={shipTo} onFrom={setShipFrom} onTo={setShipTo}
        applied={data.ship_window}
      />

      <Card title="발주가 돈이 되기까지 — 지금 어느 칸에 얼마가 걸려 있나">
        <div className="grid grid-cols-1 gap-px bg-gray-200 sm:grid-cols-2 lg:grid-cols-4">
          {data.stages.map((s) => {
            const meta = STAGE_META[s.key];
            const clickable = s.key !== "await_payment";
            const open = openStage === s.key;
            return (
              <button
                key={s.key}
                type="button"
                disabled={!clickable}
                onClick={() => setOpenStage(open ? null : s.key)}
                title={clickable ? `${meta.desc} — 눌러서 발주 목록 보기` : meta.desc}
                className={`bg-white p-4 text-left ${clickable ? "hover:bg-sky-50" : "cursor-default"} ${
                  open ? "ring-2 ring-inset ring-sky-500" : ""
                }`}
              >
                <div className="text-xs text-gray-500">
                  {meta.no} {meta.label}
                </div>
                <div className="mt-0.5 text-lg font-semibold tabular-nums text-gray-900">
                  {won(s.amount)}
                </div>
                <div className="mt-0.5 text-xs text-gray-400">
                  {isPoStage(s)
                    ? `발주 ${cnt(s.po_count)}건`
                    : `계산서 ${cnt(s.invoice_count)}건 · 지급 ${s.next_payment_date ?? NO_DATA}~${s.last_payment_date ?? NO_DATA}`}
                </div>
                {/* ★신선도 — 「지금 참인 상태」와 「마지막으로 본 상태」를 금액으로 가른다.
                    문구는 «모른다»를 말한다: 이 금액이 남아 있다는 주장이 아니라, 그 사이
                    처리됐는지 우리가 안 봐서 모른다는 뜻이다. */}
                {isPoStage(s) && (
                  num(s.stale_amount) > 0 ? (
                    <div className="mt-1.5 text-xs text-amber-700">
                      오늘 수집분 {won(s.fresh_amount)} · <b>지금 상태 모름 {won(s.stale_amount)}</b>
                      {" "}({cnt(s.stale_po_count)}건
                      {s.oldest_stale_synced_date ? `, ${s.oldest_stale_synced_date} 이후 다시 안 봄` : ""})
                    </div>
                  ) : (
                    <div className="mt-1.5 text-xs text-gray-400">전액 오늘 수집분</div>
                  )
                )}
                <div className="mt-1 text-[11px] leading-tight text-gray-400">{meta.desc}</div>
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-baseline justify-between gap-2 border-t border-gray-100 px-4 py-3">
          <div className="text-sm text-gray-700">
            <b>계산서 전 소계 (①+②+③)</b>
            <span className="ml-2 text-xs text-gray-400">
              아직 계산서가 안 나간 돈. ④지급대기는 계산서 그레인이라 여기 안 들어갑니다(더하면 이중계상).
            </span>
          </div>
          <div className="text-lg font-semibold tabular-nums text-gray-900">
            {won(data.pre_invoice_subtotal.amount)}
          </div>
        </div>
      </Card>

      {/* ★소계 밖 두 덩어리 — 색과 자리를 달리해 「합계에 넣으면 안 되는 것」임을 화면이 말한다 */}
      <Card title="소계에 넣지 않는 것">
        <div className="divide-y divide-gray-100">
          <ExtraRow
            label="확정했는데 발송 없이 닫힘"
            sub="영영 못 보내는 분입니다."
            count={data.closed_unshipped.po_count}
            amount={data.closed_unshipped.amount}
            onOpen={() => setOpenStage(openStage === "closed_unshipped" ? null : "closed_unshipped")}
            open={openStage === "closed_unshipped"}
          />
          <ExtraRow
            label="미해명 — 우리 발송 신고 > 쿠팡 인정 입고"
            sub={data.unexplained.reason}
            count={data.unexplained.po_count}
            amount={data.unexplained.amount}
            warn
            range={
              data.unexplained.oldest_po_date
                ? `발주일 ${data.unexplained.oldest_po_date} ~ ${data.unexplained.newest_po_date ?? NO_DATA}`
                : null
            }
            onOpen={() => setOpenStage(openStage === "unexplained" ? null : "unexplained")}
            open={openStage === "unexplained"}
          />
        </div>
      </Card>

      {clampLines.length > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <b>보정·결손 자백</b>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {clampLines.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </div>
      )}

      <FreshnessLine data={data} />

      {openStage && (
        <StageRows
          stage={openStage}
          shipFrom={openStage === "await_receive" ? shipFrom : ""}
          shipTo={openStage === "await_receive" ? shipTo : ""}
          onClose={() => setOpenStage(null)}
        />
      )}
    </div>
  );
}

function ExtraRow({ label, sub, count, amount, range, warn, onOpen, open }: {
  label: string; sub: string; count: number; amount: string;
  range?: string | null; warn?: boolean; onOpen: () => void; open: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`flex w-full flex-wrap items-start justify-between gap-3 px-4 py-3 text-left hover:bg-gray-50 ${
        open ? "bg-sky-50" : ""
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-sm font-medium text-gray-800">
          {label}
          {warn && <Badge tone="alert">확정 아님 — 구별 불가</Badge>}
        </div>
        <div className="mt-0.5 text-xs leading-snug text-gray-500">{sub}</div>
        {range && <div className="mt-0.5 text-xs text-gray-400">{range}</div>}
      </div>
      <div className="text-right">
        <div className={`text-base font-semibold tabular-nums ${warn ? "text-amber-700" : "text-gray-700"}`}>
          {won(amount)}
        </div>
        <div className="text-xs text-gray-400">발주 {cnt(count)}건</div>
      </div>
    </button>
  );
}

function FreshnessLine({ data }: { data: RocketPipeline }) {
  const f = data.freshness;
  return (
    // ★항목마다 span으로 감싼다 — 한 줄에 텍스트를 늘어놓으면 「최신 발송일」 같은 라벨이
    //   형제 텍스트 노드로 쪼개져, 그 항목이 화면에서 사라져도 표면 테스트가 못 잡는다.
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 rounded border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
      <span>마지막 수집 <b>{data.last_collection_date_kst ?? NO_DATA}</b></span>
      <span>발주 {f.po_synced_at_kst ?? NO_DATA}</span>
      <span>발송 {f.shipment_synced_at_kst ?? NO_DATA}</span>
      <span>최신 발송일 <b>{f.latest_shipped_date_kst ?? NO_DATA}</b></span>
      <div className="w-full text-gray-500">{f.note}</div>
    </div>
  );
}

function ShipWindowBar({ from, to, onFrom, onTo, applied }: {
  from: string; to: string; onFrom: (v: string) => void; onTo: (v: string) => void;
  applied: RocketPipeline["ship_window"];
}) {
  return (
    <div className="rounded border border-gray-200 bg-white px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600">
        <b>발송일</b>
        <input
          type="date" value={from} onChange={(e) => onFrom(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1"
          aria-label="발송일 시작"
        />
        <span>~</span>
        <input
          type="date" value={to} onChange={(e) => onTo(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1"
          aria-label="발송일 끝"
        />
        {(from || to) && (
          <button
            type="button" onClick={() => { onFrom(""); onTo(""); }}
            className="rounded border border-gray-300 px-2 py-1 hover:bg-gray-50"
          >
            창 지우기
          </button>
        )}
      </div>
      <p className="mt-1.5 text-xs leading-snug text-gray-500">
        ★이 창은 <b>③입고 대기 칸에만</b> 적용됩니다(위 화면의 발주일 기간바와 <b>다른 축</b>입니다).
        비워 두면 <b>열려 있는 것 전부</b>를 봅니다 — 기본 창을 두면 창 밖에 굳어 있는 돈이 화면에서 사라집니다.
        {applied && (
          <>
            {" "}창을 걸어도 <b>금액은 발주 전체 기준</b>입니다.
            {applied.po_with_out_of_window_shipment > 0
              ? ` 창 밖 발송이 섞인 발주 ${applied.po_with_out_of_window_shipment}건이 포함돼 있습니다.`
              : " 창 밖 발송이 섞인 발주는 없습니다."}
          </>
        )}
      </p>
    </div>
  );
}

// ── 한 칸의 발주 목록 ─────────────────────────────────────────
function StageRows({ stage, shipFrom, shipTo, onClose }: {
  stage: string; shipFrom: string; shipTo: string; onClose: () => void;
}) {
  const { data, error } = useAsyncData(
    () => fetchRocketPipelineStage(stage, { shipFrom: shipFrom || null, shipTo: shipTo || null }),
    [stage, shipFrom, shipTo],
  );
  const title = STAGE_META[stage]?.label
    ?? (stage === "unexplained" ? "미해명" : stage === "closed_unshipped" ? "발송 없이 닫힘" : stage);

  return (
    <Card
      title={`${title} — 발주 목록`}
      right={<button type="button" onClick={onClose} className="text-xs text-gray-500 hover:text-gray-800">닫기</button>}
    >
      {error ? (
        <EmptyState reason={`목록을 불러오지 못했습니다: ${error}`} hint="조회 실패는 '해당 발주 없음'과 다릅니다." />
      ) : data === null ? (
        <Loading rows={4} />
      ) : data.rows.length === 0 ? (
        <EmptyState reason="이 칸에 걸린 발주가 없습니다." hint="0건은 문제가 아니라 이 칸이 비었다는 뜻입니다." />
      ) : (
        <>
          {data.truncated && (
            <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
              전체 {cnt(data.total_count)}건 중 {cnt(data.rows.length)}건만 표시했습니다(목록 잘림).
            </div>
          )}
          <Table head={<>
            <Th>발주번호</Th><Th>발주일</Th><Th>상태</Th>
            <Th right>확정액</Th><Th right>발송액</Th><Th right>입고액</Th>
            <Th right>이 칸 금액</Th><Th>계산서</Th><Th>마지막으로 본 날</Th>
          </>}>
            {data.rows.map((r) => <StageRow key={r.purchase_order_seq} r={r} />)}
          </Table>
        </>
      )}
    </Card>
  );
}

function StageRow({ r }: { r: RocketPipelineRow }) {
  return (
    <tr className={r.is_stale ? "bg-amber-50/40" : undefined}>
      <Td>
        <div className="font-medium tabular-nums text-gray-800">{r.purchase_order_seq}</div>
        <div className="max-w-[22rem] truncate text-xs text-gray-400" title={r.first_sku_name ?? ""}>
          {r.first_sku_name ?? NO_DATA}
          {r.sku_count > 1 ? ` 외 ${r.sku_count - 1}` : ""}
        </div>
      </Td>
      <Td>{r.po_date ?? NO_DATA}</Td>
      <Td>
        {r.status_label ?? NO_DATA}
        {r.center_name && <div className="text-xs text-gray-400">{r.center_name}</div>}
      </Td>
      <Td right>{won(r.confirmed_amount)}</Td>
      <Td right>
        {/* ★발송 기록이 없는데 입고된 건은 0원이 「안 보냄」이 아니다 — 그 자리에 사유를 쓴다 */}
        {/* ★발송 기록이 없는데 입고된 건은 0원이 「안 보냄」이 아니다 — 계산에 실제로 쓰인
            하한(입고액)을 **화면에도 같이** 낸다. 이 값이 화면에 없으면 「기록 없음」만 보고
            사람이 「0원어치 보냈다」로 읽는다(적대 리뷰 1R P2-1: 계산만 하고 아무도 안 보던 값). */}
        {r.asn_missing ? (
          <span
            className="text-amber-700"
            title="입고는 잡혔는데 발송(ASN) 라인이 0건입니다. 안 보낸 것이 아니라 발송 기록이 없는 것입니다. 「안 보낸 양」은 입고액을 하한으로 써서 계산했습니다."
          >
            기록 없음
            <div className="text-xs text-gray-400">입고 기준 {won(r.effective_shipped_amount)}</div>
          </span>
        ) : (
          won(r.shipped_amount)
        )}
      </Td>
      <Td right>{won(r.received_amount)}</Td>
      <Td right><b>{won(r.stage_amount)}</b></Td>
      <Td>
        {r.has_invoice
          ? <span className="tabular-nums text-xs text-gray-600">{r.invoice_seqs.join(", ")}</span>
          : <span className="text-xs text-gray-400">미연결</span>}
      </Td>
      <Td>
        {r.is_stale
          ? <StaleBadge since={r.synced_date} />
          : <span className="text-xs text-gray-400">{r.synced_date ?? NO_DATA}</span>}
      </Td>
    </tr>
  );
}

// ══════════════════════════════════════════════════════════════
// 탭 ②  「확인요청함」 (RI)
// ══════════════════════════════════════════════════════════════
/** 확인 명령 상태 → 화면 문구. ★unknown을 「실패」로 부르지 않는다 — 다른 사실이다. */
const CONFIRM_STATE_LABEL: Record<string, { text: string; tone: "good" | "bad" | "alert" | "muted" }> = {
  pending: { text: "실행 대기", tone: "muted" },
  claimed: { text: "실행 중", tone: "muted" },
  succeeded: { text: "확인 완료", tone: "good" },
  // ★「이미 처리됨」은 성공과 **다른 사실**이다 — 우리가 누른 게 아니라 사전 GET에 버튼이 없었다.
  already_confirmed: { text: "이미 처리됨(버튼 없음)", tone: "good" },
  failed: { text: "실패(반영 안 됨)", tone: "bad" },
  // ★unknown은 실패가 아니다 — 「갔는지 모른다」다. 색을 bad로 주면 「안 됐다」로 읽힌다.
  unknown: { text: "결과 미상", tone: "alert" },
};

export function RiQueueTab() {
  // ★실행 후 목록·이력을 다시 읽는다. 「눌렀는데 화면이 그대로」면 사람은 또 누른다 —
  //   그게 이 계약이 가장 피하려는 것이다(두 번 누르기).
  const [reloadKey, setReloadKey] = useState(0);
  const [modal, setModal] = useState<RocketRiRow | null>(null);
  const { data, error } = useAsyncData(() => fetchRocketRiQueue(), [reloadKey]);

  if (error) {
    return (
      <Card title="불러오지 못했습니다">
        <EmptyState
          reason={`확인요청 목록을 불러오지 못했습니다: ${error}`}
          hint="조회 실패는 '확인할 것이 없다'와 다릅니다."
        />
      </Card>
    );
  }
  if (data === null) return <Card title="불러오는 중"><Loading rows={4} /></Card>;

  const live = data.rows.filter((r) => !r.is_stale);
  const stale = data.rows.filter((r) => r.is_stale);

  return (
    <div className="space-y-4">
      <div className="rounded border border-gray-200 bg-gray-50 px-3 py-2 text-xs leading-snug text-gray-600">
        「거래명세서확인요청」은 <b>파이프라인의 칸이 아니라 우리가 눌러야 할 일 목록</b>입니다 —
        이 건들은 계산서가 이미 나가 <b>④지급 대기</b>에 들어 있어서, 파이프라인 합계에 더하면 같은 돈을 두 번 셉니다.
        <div className="mt-1">{data.note}</div>
      </div>

      <Card title={`지금 확인이 필요한 건 — ${cnt(data.live_count)}건 · ${won(data.live_amount)}`}>
        {live.length === 0 ? (
          <EmptyState
            reason="오늘 수집분에 아직 처리할 건이 없습니다."
            hint="0건은 문제가 아니라 지금 누를 것이 없다는 뜻입니다."
          />
        ) : (
          <Table head={<>
            <Th>발주번호</Th><Th>발주일</Th><Th>입고완료</Th>
            <Th right>입고액</Th><Th>연결 계산서</Th><Th>마지막으로 본 날</Th><Th>확인</Th>
          </>}>
            {live.map((r) => (
              <RiRow key={r.purchase_order_seq} r={r} onConfirm={() => setModal(r)} />
            ))}
          </Table>
        )}
      </Card>

      {/* ★신선도가 떨어진 것은 섹션 자체를 나눈다 — 같은 표에 두면 색만으로는 안 갈린다.
          실측(2026-08-27): 이 섹션의 8건은 전부 계산서 확정·전송에 지급일까지 지난 건이었다. */}
      <Card
        title={`⚠️ 지금 상태를 모르는 건 — ${cnt(data.stale_count)}건 · ${won(data.stale_amount)}`}
        right={<Badge tone="alert">오래 안 봐서 지금도 그런지 모름</Badge>}
      >
        {stale.length === 0 ? (
          <EmptyState reason="모르는 건이 없습니다 — 전건을 오늘 다시 봤습니다." />
        ) : (
          <>
            <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs leading-snug text-amber-800">
              아래는 <b>최근 수집에서 다시 보지 못한 건</b>입니다. 화면에 뜨는 상태는
              «지금»이 아니라 <b>마지막으로 봤을 때</b>의 상태라, 그 사이 이미 처리됐을 수 있습니다.
              <b>「미종결 발주 재수집」을 한 번 돌리면</b> 지금 상태로 갱신됩니다.
              <div className="mt-1">
                ※ 오래된 발주만 여기 걸립니다 — 수집이 <b>최근 발주일 기준</b>으로 돌기 때문입니다.
                「남은 금액」이 아니라 <b>「모르는 금액」</b>입니다.
              </div>
            </div>
            <Table head={<>
              <Th>발주번호</Th><Th>발주일</Th><Th>입고완료</Th>
              <Th right>입고액</Th><Th>연결 계산서</Th><Th>마지막으로 본 날</Th><Th>확인</Th>
            </>}>
              {stale.map((r) => <RiRow key={r.purchase_order_seq} r={r} stale />)}
            </Table>
          </>
        )}
      </Card>

      <ConfirmHistoryCard reloadKey={reloadKey} />

      {modal && (
        <ConfirmModal
          row={modal}
          onClose={() => setModal(null)}
          onDone={() => { setModal(null); setReloadKey((k) => k + 1); }}
        />
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// 확인 실행 모달 — ★여기가 사람 손이다(계약 §1: 매 건 「실행」 클릭)
//   열기만 해서는 **아무 일도 일어나지 않는다**: 미리보기는 dry-run이라 supplier로 나가는 것도
//   없고 명령도 안 생긴다. 「실행」을 눌러야 명령 1건이 적재된다.
// ══════════════════════════════════════════════════════════════
function ConfirmModal({
  row, onClose, onDone,
}: { row: RocketRiRow; onClose: () => void; onDone: () => void }) {
  const [preview, setPreview] = useState<RocketConfirmPreview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  // 열자마자 dry-run 1회. 실패하면 사유를 그대로 보여주고 「실행」을 막는다.
  // ★렌더 중이 아니라 effect에서 부른다 — StrictMode 이중 렌더에 요청이 딸려 나가지 않게.
  //   (dry-run이라 나가도 무해하지만, 이 파일에서 «요청은 effect에서»를 규칙으로 둔다.)
  useEffect(() => {
    let alive = true;
    previewRocketInvoiceConfirm(row.purchase_order_seq)
      .then((p) => { if (alive) setPreview(p); })
      .catch((e) => { if (alive) setErr(String(e)); });
    return () => { alive = false; };
  }, [row.purchase_order_seq]);

  const submit = async () => {
    setSending(true);
    setErr(null);
    try {
      await requestRocketInvoiceConfirm(row.purchase_order_seq, "확인요청함 화면에서 실행");
      onDone();
    } catch (e) {
      setErr(String(e));
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-lg bg-white shadow-xl">
        <div className="border-b border-gray-200 px-5 py-3 text-sm font-semibold text-gray-800">
          거래명세서확인 — 발주 {row.purchase_order_seq}
        </div>

        <div className="space-y-3 px-5 py-4 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-500">입고액</span>
            <span className="tabular-nums font-medium text-gray-800">{won(row.received_amount)}</span>
          </div>

          {/* ★되돌릴 수 없다는 사실을 «누르기 전에» 말한다. 화면에 되돌리기 경로가 없다. */}
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs leading-snug text-red-800">
            <b>supplier에 거래명세서확인을 전송합니다 — 되돌릴 수 없습니다.</b>
            <div className="mt-1">
              CI(거래명세서 확인)에서 RI로 되돌리는 경로가 supplier 화면에 없습니다.
              실행 후에는 취소·수정이 불가능합니다.
            </div>
          </div>

          {err && (
            <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {err}
            </div>
          )}

          {/* dry-run 미리보기 = 백엔드 응답 원문. 「무엇을 보낼 것인가」를 지어내지 않는다. */}
          {preview ? (
            <div className="rounded border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-[11px] leading-relaxed text-gray-700">
              <div className="mb-1 font-sans text-xs font-medium text-gray-500">
                보낼 요청 (dry-run 미리보기 — 아직 아무것도 안 나갔습니다)
              </div>
              <div>{preview.method} {preview.path}</div>
              <div>body: {JSON.stringify(preview.payload)}</div>
              <div className="text-gray-400">purchaseOrderSeq={preview.purchase_order_seq}</div>
            </div>
          ) : !err ? (
            <div className="text-xs text-gray-400">미리보기를 불러오는 중…</div>
          ) : null}
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={sending}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            취소
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={sending || preview === null}
            className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            {sending ? "전송 중…" : "실행"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// 실행 이력 — ★감사 레코드가 DB에만 있고 화면에 없으면 미달이다(계약 §4 S2)
// ══════════════════════════════════════════════════════════════
function ConfirmHistoryCard({ reloadKey }: { reloadKey: number }) {
  const { data, error } = useAsyncData(() => fetchRocketConfirmHistory(50), [reloadKey]);

  if (error) {
    return (
      <Card title="실행 이력">
        <EmptyState
          reason={`이력을 불러오지 못했습니다: ${error}`}
          hint="조회 실패는 '실행한 적이 없다'와 다릅니다."
        />
      </Card>
    );
  }
  if (data === null) return <Card title="실행 이력"><Loading rows={2} /></Card>;
  if (data.rows.length === 0) {
    return (
      <Card title="실행 이력">
        <EmptyState reason="아직 실행한 확인이 없습니다." />
      </Card>
    );
  }

  return (
    <Card title={`실행 이력 — ${cnt(data.total)}건`}>
      <Table head={<>
        <Th>요청 시각</Th><Th>발주번호</Th><Th right>입고액</Th>
        <Th>결과</Th><Th>사전 GET</Th><Th>응답</Th><Th>종료</Th>
      </>}>
        {data.rows.map((r) => {
          const meta = CONFIRM_STATE_LABEL[r.state] ?? { text: r.state, tone: "muted" as const };
          return (
            <tr key={r.command_id}>
              <Td>{r.requested_at}</Td>
              <Td><span className="tabular-nums">{r.purchase_order_seq}</span></Td>
              <Td right>{won(r.received_amount_at_request)}</Td>
              <Td>
                {meta.tone === "muted"
                  ? <span className="text-xs text-gray-500">{meta.text}</span>
                  : <Badge tone={meta.tone}>{meta.text}</Badge>}
                {r.error && <div className="mt-0.5 text-xs text-gray-500">{r.error}</div>}
              </Td>
              <Td><span className="text-xs text-gray-500">{r.precheck ?? NO_DATA}</span></Td>
              <Td>
                <span className="text-xs text-gray-500">
                  {r.http_status ?? NO_DATA}
                  {/* ★본문 «유무»를 말한다 — 원문은 감사 레코드에 그대로 보존돼 있다. */}
                  {r.has_response_body ? " · 본문 보존됨" : ""}
                </span>
                {r.response_excerpt && (
                  <div className="max-w-[18rem] truncate font-mono text-[11px] text-gray-400"
                       title={r.response_excerpt}>
                    {r.response_excerpt}
                  </div>
                )}
              </Td>
              <Td><span className="text-xs text-gray-500">{r.finished_at ?? NO_DATA}</span></Td>
            </tr>
          );
        })}
      </Table>
      <div className="border-t border-gray-100 px-4 py-2 text-xs leading-snug text-gray-500">
        「결과 미상」은 <b>실패가 아닙니다</b> — 요청이 supplier에 갔는지 확인되지 않았다는 뜻이고,
        그래서 재수집으로 실상태를 확인하기 전까지 그 건은 다시 실행할 수 없습니다(자동 재시도 없음).
        실행 명령은 임대 후 {data.lease_ttl_minutes}분 안에 보고가 없으면 「결과 미상」으로 종결됩니다.
      </div>
    </Card>
  );
}

function RiRow({ r, stale, onConfirm }: {
  r: RocketRiRow; stale?: boolean; onConfirm?: () => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  return (
    <tr className={stale ? "bg-amber-50/40" : undefined}>
      <Td>
        <div className="font-medium tabular-nums text-gray-800">{r.purchase_order_seq}</div>
        <div className="max-w-[22rem] truncate text-xs text-gray-400" title={r.first_sku_name ?? ""}>
          {r.first_sku_name ?? NO_DATA}
          {r.sku_count > 1 ? ` 외 ${r.sku_count - 1}` : ""}
        </div>
      </Td>
      <Td>{r.po_date ?? NO_DATA}</Td>
      <Td>{r.receiving_finished_date ?? NO_DATA}</Td>
      <Td right>{won(r.received_amount)}</Td>
      <Td>
        {r.invoices.length === 0 && r.invoice_rows_missing.length === 0 ? (
          <span className="text-xs text-gray-400">미연결</span>
        ) : (
          <div className="space-y-1">
            {r.invoices.map((iv) => {
              const paid = iv.payment_date != null && iv.payment_date <= today;
              return (
                <div key={iv.invoice_seq} className="text-xs">
                  <span className="tabular-nums text-gray-700">{iv.invoice_seq}</span>
                  <span className="text-gray-400">
                    {" "}· 작성 {iv.issue_date ?? NO_DATA}
                    {" "}· 확정 {iv.tax_invoice_confirmed_date ?? NO_DATA}
                    {" "}· 지급 {iv.payment_date ?? NO_DATA}
                  </span>
                  {/* ★「지급일이 지났다」가 이 행이 죽었다고 보는 근거다 — 판정을 화면이 말한다 */}
                  {paid && <span className="ml-1 text-amber-700">지급일 경과</span>}
                </div>
              );
            })}
            {r.invoice_rows_missing.length > 0 && (
              <div className="text-xs text-gray-400">
                정산행 미수집 {r.invoice_rows_missing.join(", ")} — 「미발행」이 아니라 「모름」입니다
              </div>
            )}
          </div>
        )}
      </Td>
      <Td>
        {r.is_stale
          ? <StaleBadge since={r.synced_date} />
          : <span className="text-xs text-gray-400">{r.synced_date ?? NO_DATA}</span>}
      </Td>
      {/* ★확인 칸 — 버튼이 안 뜨면 «왜 안 뜨는지»를 반드시 같이 낸다.
          버튼만 조용히 사라지면 사람은 이유를 모르고, 그건 이 화면의 정직성 규칙 4)를 어긴다. */}
      <Td>
        <ConfirmCell r={r} stale={stale} onConfirm={onConfirm} />
      </Td>
    </tr>
  );
}

function ConfirmCell({ r, stale, onConfirm }: {
  r: RocketRiRow; stale?: boolean; onConfirm?: () => void;
}) {
  const c = r.confirm;
  // 응답에 confirm이 없으면(구버전 백엔드) 버튼을 띄우지 않는다 — 모르면 안 누른다.
  if (!c) return <span className="text-xs text-gray-400">{NO_DATA}</span>;

  if (!c.can_request) {
    const meta = c.state ? CONFIRM_STATE_LABEL[c.state] : undefined;
    return (
      <div className="space-y-0.5">
        <div className="text-xs text-gray-500">{c.blocked_reason ?? NO_DATA}</div>
        {meta && (
          <div className="text-xs text-gray-400">
            직전: {meta.text}{c.last_finished_at ? ` (${c.last_finished_at})` : ""}
          </div>
        )}
      </div>
    );
  }

  // 굳은 섹션에는 onConfirm을 주지 않는다 — 굳은 원장은 실행 근거가 못 된다(계약 §2).
  if (stale || !onConfirm) {
    return <span className="text-xs text-gray-500">재수집 후 확인 가능</span>;
  }

  const done = c.state === "succeeded" || c.state === "already_confirmed";
  return (
    <div className="space-y-0.5">
      <button
        type="button"
        onClick={onConfirm}
        className="rounded border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
      >
        확인
      </button>
      {done && (
        <div className="text-xs text-gray-400">
          직전: {CONFIRM_STATE_LABEL[c.state as string]?.text}
        </div>
      )}
    </div>
  );
}
