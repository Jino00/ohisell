// RocketRecon.tsx — 로켓배송(1P) 통합 대사 화면 (조회 전용).
//
// Jino 원문: "상품별 발주내역, 납품 내역, 거래명세서 발행 내역, 계산서 발행내역을
//   한 화면에서 볼 수 있게 화면을 구성해줘."
// 지금까지 이 넷은 서로 다른 그레인(PO / 발주상세 / 계산서)에 흩어져 있어 대조가 불가능했다.
// 이 화면이 상품(SKU)을 축으로 셋을 접고, 행을 펼치면 그 상품이 들어간 발주 하나하나까지
// (발주일·진행 단계·발주/입고 수량·연결 계산서 확정일·전송 표기) 내려간다.
//
// ★이 화면의 정직성 규칙 3개 — 숫자를 예쁘게 만들려고 어기지 말 것(원칙22):
//   1) 상품별 **입고수량은 원천에 없다.** 입고는 발주(PO) 단위로만 오고, 멀티SKU 발주는
//      상품별로 쪼갤 근거가 없다. 그래서 단일SKU 발주에서만 귀속하고 나머지는 "—"로 둔다.
//      절대 안분(按分)해서 그럴듯한 숫자를 만들지 않는다.
//   2) **미수집 ≠ 0.** 납품가능수량이 아직 안 들어온 라인, 정산행이 아직 없는 계산서번호는
//      0이 아니라 "—" + 미수집 건수로 표시한다.
//   3) **드리프트는 단계로 갈린다.** 입고 전 단계(발주확정·거래처확인요청)에서 발주≠입고는
//      당연하다. 요약 타일이 강조하는 값은 "거래명세서확인 단계인데 발주≠입고"뿐이다.
import { useState } from "react";
import { Card, Stat, Table, Th, Td, Loading, EmptyState, Button, Badge } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import {
  fetchRocketRecon, fetchRocketReconSku,
  type ReconSkuRow, type RocketRecon,
} from "../lib/api";

// ── 포매터(로컬) ───────────────────────────────────────────────
// lib/format.ts는 naver-ad 스코프 전용 계약이라 여기선 로컬 정의를 쓴다(그 파일 상단 경고).
const NO_DATA = "—";
const n = (v: number | null | undefined) => (v == null ? NO_DATA : v.toLocaleString("ko-KR"));
/** 금액은 Decimal → string으로 온다. 정밀도 보존을 위해 숫자 변환은 표시 직전에만. */
const won = (v: string | number | null | undefined) => {
  if (v == null) return NO_DATA;
  const num = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(num) ? `${Math.round(num).toLocaleString("ko-KR")}원` : NO_DATA;
};
const pct = (v: number | null | undefined) => (v == null ? NO_DATA : `${(v * 100).toFixed(1)}%`);

function isoKST(d: Date): string {
  const kst = new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return `${kst.getFullYear()}-${String(kst.getMonth() + 1).padStart(2, "0")}-${String(kst.getDate()).padStart(2, "0")}`;
}

function daysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return isoKST(d);
}

export default function RocketRecon() {
  const [from, setFrom] = useState(daysAgo(89));   // 기본 최근 90일(발주 기준)
  const [to, setTo] = useState(isoKST(new Date()));
  const [driftOnly, setDriftOnly] = useState(false);
  const [unconfirmedOnly, setUnconfirmedOnly] = useState(false);

  const { data, error } = useAsyncData(
    () => fetchRocketRecon({ from, to, driftOnly, unconfirmedOnly }),
    [from, to, driftOnly, unconfirmedOnly],
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-gray-900">로켓배송(1P) 통합 대사</h1>
        <p className="mt-1 text-sm text-gray-500">
          발주 → 납품 → 거래명세서 → 계산서를 상품(SKU) 기준 한 화면에서 대조합니다. 조회 전용입니다.
        </p>
      </div>

      <PeriodBar
        from={from} to={to} onFrom={setFrom} onTo={setTo}
        driftOnly={driftOnly} onDriftOnly={setDriftOnly}
        unconfirmedOnly={unconfirmedOnly} onUnconfirmedOnly={setUnconfirmedOnly}
      />

      {error ? (
        <Card title="불러오지 못했습니다">
          <EmptyState
            reason={`대사 데이터를 불러오지 못했습니다: ${error}`}
            hint="새로고침하거나 백엔드 상태를 확인하세요. (조회 실패는 '데이터 없음'과 다릅니다)"
          />
        </Card>
      ) : data === null ? (
        <Card title="불러오는 중"><Loading rows={6} /></Card>
      ) : (
        <>
          <SummaryTiles data={data} />
          <StatusBreakdown data={data} />
          <SkuTable data={data} from={from} to={to} />
        </>
      )}
    </div>
  );
}

// ── 기간·필터 바 ───────────────────────────────────────────────
function PeriodBar({
  from, to, onFrom, onTo, driftOnly, onDriftOnly, unconfirmedOnly, onUnconfirmedOnly,
}: {
  from: string; to: string; onFrom: (v: string) => void; onTo: (v: string) => void;
  driftOnly: boolean; onDriftOnly: (v: boolean) => void;
  unconfirmedOnly: boolean; onUnconfirmedOnly: (v: boolean) => void;
}) {
  const preset = (days: number) => { onFrom(daysAgo(days - 1)); onTo(isoKST(new Date())); };
  return (
    <Card title="조회 조건">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <label className="text-xs text-gray-500">발주일</label>
        <input
          type="date" value={from} onChange={(e) => onFrom(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        />
        <span className="text-gray-400">~</span>
        <input
          type="date" value={to} onChange={(e) => onTo(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        />
        <div className="flex gap-1">
          <Button onClick={() => preset(30)}>30일</Button>
          <Button onClick={() => preset(90)}>90일</Button>
          <Button onClick={() => preset(365)}>1년</Button>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-gray-600">
            <input type="checkbox" checked={driftOnly} onChange={(e) => onDriftOnly(e.target.checked)} />
            발주≠입고만
          </label>
          <label className="flex items-center gap-1.5 text-xs text-gray-600">
            <input
              type="checkbox" checked={unconfirmedOnly}
              onChange={(e) => onUnconfirmedOnly(e.target.checked)}
            />
            계산서 미확정만
          </label>
        </div>
      </div>
      <p className="px-4 pb-3 text-xs text-gray-400">
        기간은 <b>발주일(KST)</b> 기준입니다. 발주→입고→명세서→계산서 확정까지 수 주가 걸려 기본 창을 90일로 둡니다.
        필터는 아래 상품 표에만 적용되며 요약 타일은 항상 기간 전체 기준입니다.
      </p>
    </Card>
  );
}

/** 건수 타일. 0이면 회색(idle) + "왜 0인가"가 **타입으로** 강제된다(Stat의 계약).
 *  0을 빨갛게 칠하지 않는 이유: 0건은 나쁜 게 아니라 아직 아무 문제가 없다는 뜻이다. */
function CountStat({ label, value, sub, tone, isZero, zeroReason }: {
  label: string; value: string; sub?: string;
  tone: "good" | "bad" | "warn"; isZero: boolean; zeroReason: string;
}) {
  return isZero ? (
    <Stat label={label} value={value} sub={sub} tone="idle" isEmpty reason={zeroReason} />
  ) : (
    <Stat label={label} value={value} sub={sub} tone={tone} />
  );
}

// ── 요약 타일 ─────────────────────────────────────────────────
function SummaryTiles({ data }: { data: RocketRecon }) {
  const s = data.summary;
  const cov = s.detail_coverage;
  const inv = s.invoice;
  const empty = s.po_count === 0;

  if (empty) {
    return (
      <Card title="요약">
        <EmptyState
          reason={`${data.period.from} ~ ${data.period.to} 기간에 발주(PO)가 없습니다.`}
          hint={
            s.no_date_po_count > 0
              ? `발주일이 확인되지 않은 발주가 ${s.no_date_po_count}건 있습니다 — 어떤 기간에도 잡히지 않습니다.`
              : "기간을 넓혀 보세요."
          }
        />
      </Card>
    );
  }

  return (
    <Card title={`요약 — 발주 ${n(s.po_count)}건 (발주일 ${data.period.from} ~ ${data.period.to})`}>
      <div className="grid grid-cols-2 gap-4 px-4 py-4 md:grid-cols-3 lg:grid-cols-5">
        <Stat label="발주 수량" value={n(s.order_qty)} sub={won(s.order_amount)} />
        <Stat label="납품(입고) 수량" value={n(s.received_qty)} sub={won(s.receiving_amount)} />
        <CountStat
          label="미입고 수량"
          value={n(s.unreceived_qty)}
          sub={won(s.unreceived_amount)}
          tone="warn"
          isZero={s.unreceived_qty === 0}
          zeroReason="발주 수량이 전부 입고되었습니다."
        />
        <CountStat
          label="발주≠입고 (명세서 확인 단계)"
          value={`${n(s.drift_po_count_settled_stage)}건`}
          sub={`해당 단계 발주 ${n(s.settled_stage_po_count)}건 중`}
          tone="bad"
          isZero={s.drift_po_count_settled_stage === 0}
          zeroReason="거래명세서 확인까지 간 발주는 전부 발주=입고입니다."
        />
        <CountStat
          label="계산서 미연결 발주"
          value={`${n(inv.po_without_invoice_count)}건`}
          sub={`매핑된 계산서 ${n(inv.mapped_invoice_count)}건 · ${won(inv.settled_amount)}`}
          tone="warn"
          isZero={inv.po_without_invoice_count === 0}
          zeroReason="기간 내 모든 발주가 계산서에 묶였습니다."
        />
      </div>

      <div className="grid grid-cols-2 gap-4 border-t border-gray-100 px-4 py-4 md:grid-cols-4">
        <CountStat
          label="세금계산서 미확정"
          value={`${n(inv.invoice_unconfirmed_count)}건`}
          sub="확정일이 아직 없는 계산서"
          tone="warn"
          isZero={inv.invoice_unconfirmed_count === 0}
          zeroReason="매핑된 계산서가 모두 확정일을 가지고 있습니다."
        />
        <CountStat
          label="전송성공 미표기"
          value={`${n(inv.invoice_not_marked_transmitted_count)}건`}
          sub="미표기 ≠ 전송 실패"
          tone="warn"
          isZero={inv.invoice_not_marked_transmitted_count === 0}
          zeroReason="매핑된 계산서가 모두 전송성공으로 표기돼 있습니다."
        />
        <CountStat
          label="계산서 정산행 미수집"
          value={`${n(inv.invoice_missing_row_count)}건`}
          sub="번호는 있는데 정산 내역이 아직 안 옴"
          tone="warn"
          isZero={inv.invoice_missing_row_count === 0}
          zeroReason="매핑된 계산서번호가 전부 정산 내역과 연결됐습니다."
        />
        <Stat
          label="발주상세(상품별) 수집률"
          value={pct(cov.po_coverage_pct)}
          sub={`${n(cov.pos_with_detail_count)}/${n(cov.pos_with_detail_count + cov.pos_without_detail_count)} 발주 · ${n(cov.sku_count)} SKU`}
          tone={(cov.po_coverage_pct ?? 1) < 1 ? "warn" : "good"}
        />
      </div>

      <div className="border-t border-gray-100 px-4 py-3 text-xs text-gray-500 space-y-1">
        <p>
          <b>요약 타일은 발주(PO) 단위 전체</b>, 아래 상품 표는 <b>발주상세가 수집된 발주만</b>입니다 —
          합계가 다른 것이 정상이며, 그 차이가 위 &lsquo;발주상세 수집률&rsquo;입니다.
          {cov.pos_without_detail_count > 0 && (
            <> 상품별 내역이 없는 발주 {n(cov.pos_without_detail_count)}건은 아래 표에 나오지 않습니다.</>
          )}
        </p>
        <p>
          발주≠입고 전체는 {n(s.drift_po_count)}건이지만, 입고 전 단계(발주확정·거래처확인요청)의
          불일치는 당연합니다. 위 타일은 <b>거래명세서 확인까지 간 발주</b>만 셉니다.
        </p>
        {s.no_date_po_count > 0 && (
          <p>발주일 미상 발주 {n(s.no_date_po_count)}건은 어떤 기간에도 잡히지 않습니다.</p>
        )}
      </div>
    </Card>
  );
}

// ── 상태별(거래명세서 단계 포함) ────────────────────────────────
function StatusBreakdown({ data }: { data: RocketRecon }) {
  const rows = data.summary.by_status;
  if (rows.length === 0) return null;
  return (
    <Card title="진행 단계별 발주">
      <Table
        head={
          <>
            <Th>단계</Th><Th right>발주 건수</Th><Th right>발주 수량</Th><Th right>입고 수량</Th>
            <Th right>발주 금액</Th><Th right>입고 금액</Th><Th right>발주≠입고</Th>
          </>
        }
      >
        {rows.map((r) => (
          <tr key={r.status ?? "unknown"}>
            <Td>
              <span className="mr-2">{r.status_description ?? NO_DATA}</span>
              <span className="text-xs text-gray-400">{r.status}</span>
              {r.is_settled_stage && <span className="ml-2"><Badge tone="owner">입고 완료 단계</Badge></span>}
            </Td>
            <Td right>{n(r.po_count)}</Td>
            <Td right>{n(r.order_qty)}</Td>
            <Td right>{n(r.received_qty)}</Td>
            <Td right>{won(r.order_amount)}</Td>
            <Td right>{won(r.receiving_amount)}</Td>
            <Td right>
              <span className={r.is_settled_stage && r.drift_po_count > 0 ? "text-judge-bad font-medium" : "text-gray-500"}>
                {n(r.drift_po_count)}
              </span>
            </Td>
          </tr>
        ))}
      </Table>
      <p className="px-4 py-2 text-xs text-gray-400">
        진행 순서: 거래처확인요청 → 발주확정 → 거래명세서확인. 마지막 단계가 곧 입고·명세서 확인이
        끝났다는 뜻이라, 이 단계의 발주≠입고만 붉게 표시합니다.
      </p>
    </Card>
  );
}

// ── 상품(SKU) 표 + 행 확장 ──────────────────────────────────────
function SkuTable({ data, from, to }: { data: RocketRecon; from: string; to: string }) {
  const [openSku, setOpenSku] = useState<string | null>(null);
  const f = data.filters;

  const title =
    f.drift_only || f.unconfirmed_only
      ? `상품별 대사 — ${n(f.sku_count_shown)}/${n(f.sku_count_total)} SKU (필터 적용)`
      : `상품별 대사 — ${n(f.sku_count_total)} SKU`;

  if (data.skus.length === 0) {
    return (
      <Card title={title}>
        <EmptyState
          reason={
            f.drift_only || f.unconfirmed_only
              ? `필터 조건에 맞는 상품이 없습니다 (전체 ${n(f.sku_count_total)} SKU 중 0건).`
              : "이 기간에 상품별 발주상세가 수집된 발주가 없습니다."
          }
          hint={
            f.drift_only || f.unconfirmed_only
              ? "필터를 해제하면 전체 상품을 볼 수 있습니다."
              : "상품별 내역은 발주상세 수집이 시작된 이후 발주부터 존재합니다 — 그 전 발주는 발주 단위 수량만 있습니다."
          }
        />
      </Card>
    );
  }

  return (
    <Card title={title}>
      <Table
        head={
          <>
            <Th>상품(SKU)</Th>
            <Th right>발주</Th>
            <Th right>납품가능</Th>
            <Th right>입고</Th>
            <Th right>발주−입고</Th>
            <Th right>발주 금액</Th>
            <Th>계산서</Th>
            <Th> </Th>
          </>
        }
      >
        {data.skus.map((r) => (
          <SkuRows
            key={r.product_number}
            row={r}
            open={openSku === r.product_number}
            onToggle={() => setOpenSku(openSku === r.product_number ? null : r.product_number)}
            from={from}
            to={to}
          />
        ))}
      </Table>
      <p className="px-4 py-3 text-xs text-gray-400">
        <b>입고</b> 열은 <b>단일 상품 발주에서만</b> 채워집니다 — 여러 상품이 섞인 발주는 입고수량이
        발주 단위로만 와서 상품별로 나눌 근거가 없기 때문입니다(&ldquo;{NO_DATA}&rdquo;는 0이 아니라
        &lsquo;모름&rsquo;). 각 상품의 발주 단위 실제 입고는 행을 펼쳐 확인하세요.
      </p>
    </Card>
  );
}

function SkuRows({ row, open, onToggle, from, to }: {
  row: ReconSkuRow; open: boolean; onToggle: () => void; from: string; to: string;
}) {
  const invoiceIssues =
    row.po_without_invoice_count + row.invoice_unconfirmed_count +
    row.invoice_missing_row_count + row.invoice_not_marked_transmitted_count;

  return (
    <>
      <tr className={open ? "bg-blue-50/40" : undefined}>
        <Td>
          <button type="button" onClick={onToggle} className="text-left">
            <span className="font-medium text-gray-900">{row.product_number}</span>
            <span className="ml-2 text-xs text-gray-500">발주 {n(row.po_count)}건</span>
            <div className="text-xs text-gray-500 max-w-md truncate">
              {row.product_name ?? NO_DATA}
            </div>
          </button>
        </Td>
        <Td right>{n(row.order_qty)}</Td>
        <Td right>
          {row.confirmed_missing_lines > 0 && row.confirmed_qty === 0
            ? NO_DATA
            : n(row.confirmed_qty)}
          {row.confirmed_missing_lines > 0 && (
            <div className="text-xs text-gray-400">미수집 {n(row.confirmed_missing_lines)}줄</div>
          )}
        </Td>
        <Td right>
          {row.received_qty == null ? (
            <span className="text-gray-400">{NO_DATA}</span>
          ) : (
            n(row.received_qty)
          )}
          {row.received_unattributable_po_count > 0 && (
            <div className="text-xs text-gray-400">
              발주 {n(row.received_unattributable_po_count)}건 귀속불가
            </div>
          )}
        </Td>
        <Td right>
          {row.drift_qty == null ? (
            <span className="text-gray-400">{NO_DATA}</span>
          ) : row.drift_qty === 0 ? (
            <span className="text-gray-400">0</span>
          ) : (
            <span className="text-judge-bad font-medium">{n(row.drift_qty)}</span>
          )}
        </Td>
        <Td right>{won(row.order_amount)}</Td>
        <Td>
          {invoiceIssues === 0 ? (
            <span className="text-xs text-gray-400">정상 {n(row.invoice_count)}건</span>
          ) : (
            <div className="flex flex-wrap gap-1">
              {row.po_without_invoice_count > 0 && (
                <Badge>미연결 {n(row.po_without_invoice_count)}</Badge>
              )}
              {row.invoice_unconfirmed_count > 0 && (
                <Badge>미확정 {n(row.invoice_unconfirmed_count)}</Badge>
              )}
              {row.invoice_missing_row_count > 0 && (
                <Badge>정산 미수집 {n(row.invoice_missing_row_count)}</Badge>
              )}
              {row.invoice_not_marked_transmitted_count > 0 && (
                <Badge>전송 미표기 {n(row.invoice_not_marked_transmitted_count)}</Badge>
              )}
            </div>
          )}
        </Td>
        <Td>
          <Button variant="ghost" onClick={onToggle}>{open ? "닫기 ▲" : "발주내역 ▼"}</Button>
        </Td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8} className="bg-gray-50 px-4 py-3 border-b border-gray-100">
            <SkuDetail productNumber={row.product_number} from={from} to={to} />
          </td>
        </tr>
      )}
    </>
  );
}

function SkuDetail({ productNumber, from, to }: { productNumber: string; from: string; to: string }) {
  const { data, error } = useAsyncData(
    () => fetchRocketReconSku(productNumber, from, to),
    [productNumber, from, to],
  );

  if (error) {
    return <EmptyState reason={`발주내역을 불러오지 못했습니다: ${error}`} hint="잠시 후 다시 시도하세요." />;
  }
  if (data === null) return <Loading rows={3} label="발주내역 불러오는 중…" />;
  if (data.rows.length === 0) {
    return (
      <EmptyState
        reason="이 기간에 이 상품의 발주상세가 없습니다."
        hint="발주 자체가 없었다는 뜻은 아닙니다 — 발주상세가 아직 수집되지 않은 발주일 수 있습니다."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-gray-500">
            <th className="px-2 py-1 text-left">발주번호 / 발주일</th>
            <th className="px-2 py-1 text-left">진행 단계</th>
            <th className="px-2 py-1 text-right">이 상품 발주</th>
            <th className="px-2 py-1 text-right">납품가능</th>
            <th className="px-2 py-1 text-right">발주 전체(발주/입고)</th>
            <th className="px-2 py-1 text-left">거래명세서·계산서</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr key={r.purchase_order_seq} className="border-t border-gray-200 align-top">
              <td className="px-2 py-2">
                <div className="font-medium text-gray-900">{r.purchase_order_seq}</div>
                <div className="text-xs text-gray-500">{r.po_created_date ?? NO_DATA}</div>
                {r.center_name && <div className="text-xs text-gray-400">{r.center_name}</div>}
              </td>
              <td className="px-2 py-2">
                <span className={r.is_settled_stage ? "text-gray-900" : "text-gray-500"}>
                  {r.status_description ?? NO_DATA}
                </span>
                {r.sku_count > 1 && (
                  <div className="text-xs text-gray-400">이 발주에 {n(r.sku_count)}개 상품</div>
                )}
              </td>
              <td className="px-2 py-2 text-right tabular-nums">{n(r.line_order_qty)}</td>
              <td className="px-2 py-2 text-right tabular-nums">
                {r.line_confirmed_qty == null
                  ? <span className="text-gray-400" title="아직 수집되지 않음 (0이 아님)">{NO_DATA}</span>
                  : n(r.line_confirmed_qty)}
              </td>
              <td className="px-2 py-2 text-right tabular-nums">
                <div>
                  {n(r.po_order_qty)} / {n(r.po_received_qty)}
                  {r.po_drift_qty !== 0 && (
                    <span className={r.is_settled_stage ? "ml-1 text-judge-bad" : "ml-1 text-gray-400"}>
                      ({r.po_drift_qty > 0 ? "-" : "+"}{n(Math.abs(r.po_drift_qty))})
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-400">{won(r.po_order_amount)}</div>
              </td>
              <td className="px-2 py-2">
                {r.invoices.length === 0 ? (
                  <span className="text-xs text-gray-400">계산서 미연결 (아직 정산에 안 묶임)</span>
                ) : (
                  <div className="space-y-1">
                    {r.invoices.map((v) => (
                      <div key={v.invoice_seq} className="text-xs">
                        <span className="font-medium text-gray-800">[{v.invoice_seq}]</span>{" "}
                        {!v.found ? (
                          <span className="text-gray-400" title="계산서번호는 발주에 붙었으나 정산 내역이 아직 수집되지 않음">
                            정산 내역 미수집
                          </span>
                        ) : (
                          <>
                            <span className="text-gray-600">
                              작성 {v.issue_date ?? NO_DATA} · 확정{" "}
                              {v.tax_invoice_confirmed_date ?? (
                                <span className="text-judge-warn">미확정</span>
                              )}
                            </span>{" "}
                            <span className="text-gray-500">{won(v.payment_amount)}</span>{" "}
                            {v.tax_invoice_transmitted === true ? (
                              <span className="text-judge-good">전송성공</span>
                            ) : (
                              <span className="text-gray-400" title="확정 전에는 상태 텍스트가 없습니다 — 전송 실패가 아닙니다">
                                전송 미표기
                              </span>
                            )}
                            {v.bill_issue_type && (
                              <span className="ml-1 text-gray-400">{v.bill_issue_type}</span>
                            )}
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-gray-400">
        &lsquo;발주 전체&rsquo;의 입고수량은 <b>발주 단위</b> 값입니다 — 여러 상품이 섞인 발주라면 이 상품만의
        입고가 아닙니다. 괄호는 발주 대비 미입고분이며, 입고 전 단계에서는 회색(당연)입니다.
      </p>
    </div>
  );
}
