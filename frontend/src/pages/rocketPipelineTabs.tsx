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
//   3) **굳은 것을 산 것처럼 보여주지 않는다.** 수집 창이 발주일 기준이라 오래된 미종결 PO는
//      상태가 마지막 수집일에 멈춘다. 칸마다 「오늘 갱신분 / 굳은 분」을 금액으로 가르고,
//      행에는 「YYYY-MM-DD 이후 미확인」 배지를 붙인다. RI 탭은 아예 섹션을 나눈다 —
//      실측(2026-08-27) RI 12건 중 8건이 이미 지급까지 끝난 유령이었다.
//   4) **보정·절단을 자백한다.** clamp(음수 절단)·ASN 미수집 보정·목록 잘림은 전부 화면에
//      한 줄로 쓴다. 조용히 처리하면 「깨끗한 화면」이 곧 「거짓말하는 화면」이 된다.
import { useState } from "react";
import { Card, Table, Th, Td, Loading, EmptyState, Badge } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import {
  fetchRocketPipeline, fetchRocketPipelineStage, fetchRocketRiQueue, isPoStage,
  type RocketPipeline, type RocketPipelineRow, type RocketRiRow,
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

/** 「YYYY-MM-DD 이후 미확인」 배지. 굳은 행에만 붙는다. */
function StaleBadge({ since }: { since: string | null }) {
  return (
    <Badge tone="alert">{since ? `${since} 이후 미확인` : "확인 시각 미상"}</Badge>
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
                {/* ★신선도 — 「지금 참인 상태」와 「마지막으로 본 상태」를 금액으로 가른다 */}
                {isPoStage(s) && (
                  num(s.stale_amount) > 0 ? (
                    <div className="mt-1.5 text-xs text-amber-700">
                      최신 {won(s.fresh_amount)} · <b>굳음 {won(s.stale_amount)}</b>
                      {" "}({cnt(s.stale_po_count)}건
                      {s.oldest_stale_synced_date ? `, ${s.oldest_stale_synced_date} 이후 미확인` : ""})
                    </div>
                  ) : (
                    <div className="mt-1.5 text-xs text-gray-400">전액 최신 수집분</div>
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
            <Th right>이 칸 금액</Th><Th>계산서</Th><Th>마지막 확인</Th>
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
        {r.asn_missing
          ? <span className="text-amber-700" title="입고는 잡혔는데 발송(ASN) 라인이 0건입니다. 안 보낸 것이 아니라 발송 기록이 없는 것입니다.">기록 없음</span>
          : won(r.shipped_amount)}
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
export function RiQueueTab() {
  const { data, error } = useAsyncData(() => fetchRocketRiQueue(), []);

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
            reason="마지막 수집분에서 확인 대기 중인 발주가 없습니다."
            hint="0건은 문제가 아니라 지금 누를 것이 없다는 뜻입니다."
          />
        ) : (
          <Table head={<>
            <Th>발주번호</Th><Th>발주일</Th><Th>입고완료</Th>
            <Th right>입고액</Th><Th>연결 계산서</Th><Th>마지막 확인</Th>
          </>}>
            {live.map((r) => <RiRow key={r.purchase_order_seq} r={r} />)}
          </Table>
        )}
      </Card>

      {/* ★굳은 것은 섹션 자체를 나눈다 — 같은 표에 두면 색만으로는 안 갈린다.
          실측(2026-08-27): 이 섹션의 8건은 전부 계산서 확정·전송에 지급일까지 지난 건이었다. */}
      <Card
        title={`⚠️ 상태가 굳은 건 — ${cnt(data.stale_count)}건 · ${won(data.stale_amount)}`}
        right={<Badge tone="alert">지금 참인 상태가 아닐 수 있음</Badge>}
      >
        {stale.length === 0 ? (
          <EmptyState reason="굳은 건이 없습니다 — 전건이 마지막 수집분입니다." />
        ) : (
          <>
            <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs leading-snug text-amber-800">
              아래는 수집 창(발주일 기준) 밖이라 <b>마지막으로 본 상태</b>가 그대로 남아 있는 건입니다.
              이미 처리돼 닫혔을 수 있으니 <b>누르기 전에 「미종결 발주 재수집」을 한 번 돌리세요.</b>
              연결 계산서의 <b>지급일이 이미 지났다면</b> 그 건은 사실상 끝난 건입니다.
            </div>
            <Table head={<>
              <Th>발주번호</Th><Th>발주일</Th><Th>입고완료</Th>
              <Th right>입고액</Th><Th>연결 계산서</Th><Th>마지막 확인</Th>
            </>}>
              {stale.map((r) => <RiRow key={r.purchase_order_seq} r={r} stale />)}
            </Table>
          </>
        )}
      </Card>
    </div>
  );
}

function RiRow({ r, stale }: { r: RocketRiRow; stale?: boolean }) {
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
    </tr>
  );
}
