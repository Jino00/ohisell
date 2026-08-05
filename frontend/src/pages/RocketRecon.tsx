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
import { useMemo, useState } from "react";
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
/** 비율도 Decimal → 문자열("0.0036")로 온다. 암묵 강제변환에 기대지 않고 명시적으로 변환한다. */
const pct = (v: string | number | null | undefined) => {
  if (v == null) return NO_DATA;
  const num = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : NO_DATA;
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
  /** 오늘까지의 최근 N일. N=1이면 오늘 하루. */
  const preset = (days: number) => { onFrom(daysAgo(days - 1)); onTo(isoKST(new Date())); };
  /** 하루짜리 창(어제처럼 시작=끝). 최근 N일과 달리 오늘을 포함하지 않는다. */
  const day = (agoDays: number) => { const d = daysAgo(agoDays); onFrom(d); onTo(d); };
  const today = isoKST(new Date());
  const active = (f: string, t: string) => from === f && to === t;
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
        {/* 지금 걸린 기간과 같은 프리셋은 눌린 상태로 보인다 — 어느 창을 보고 있는지가
            날짜 두 개를 읽어야만 알 수 있으면 오독한다. */}
        <div className="flex flex-wrap gap-1">
          <Button variant={active(today, today) ? "primary" : "secondary"} onClick={() => preset(1)}>오늘</Button>
          <Button variant={active(daysAgo(1), daysAgo(1)) ? "primary" : "secondary"} onClick={() => day(1)}>어제</Button>
          <Button variant={active(daysAgo(6), today) ? "primary" : "secondary"} onClick={() => preset(7)}>7일</Button>
          <Button variant={active(daysAgo(29), today) ? "primary" : "secondary"} onClick={() => preset(30)}>30일</Button>
          <Button variant={active(daysAgo(89), today) ? "primary" : "secondary"} onClick={() => preset(90)}>90일</Button>
          <Button variant={active(daysAgo(364), today) ? "primary" : "secondary"} onClick={() => preset(365)}>1년</Button>
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
        &lsquo;오늘·어제·7일&rsquo;은 <b>그 날짜에 난 발주</b>만 보는 창이라 입고·계산서가 아직 비어 있는 것이 정상이며,
        발주 자체가 없던 날이면 표가 비어 보입니다(수집 실패와 다릅니다).
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
          label="발주≠입고 (입고 완료 단계)"
          value={`${n(s.drift_po_count_settled_stage)}건`}
          sub={`해당 단계 발주 ${n(s.settled_stage_po_count)}건 중`}
          tone="bad"
          isZero={s.drift_po_count_settled_stage === 0}
          zeroReason="입고가 끝난 발주는 전부 발주=입고입니다."
        />
        <CountStat
          label="계산서 미연결 발주"
          value={`${n(inv.po_without_invoice_count)}건`}
          sub={`매핑된 계산서 ${n(inv.mapped_invoice_count)}건 · ${won(inv.settled_amount)} (묶음 정산이라 기간 밖 발주분 포함 가능)`}
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
          tone={Number(cov.po_coverage_pct ?? 1) < 1 ? "warn" : "good"}
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
          불일치는 당연합니다. 위 타일은 <b>입고가 끝난 단계</b>(거래명세서확인·거래명세서확인요청)의
          발주만 셉니다.
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
/** 검색·정렬 키. 서버는 발주 금액 desc로 주므로 그 순서를 '기본'으로 보존한다
 *  (정렬을 끄면 원래 보던 화면으로 정확히 돌아가야 하기 때문).
 *  ★'납품 안 된 건'은 화면에 **두 종류**가 있어 키도 둘이다 — 하나로 합치면 둘 중 하나가 거짓이 된다:
 *    · undelivered        = 발주−입고 전체(입고 전 단계 포함) — "아직 안 들어온 것"
 *    · undelivered_settled = 입고가 끝난 단계의 발주−입고    — "들어왔어야 하는데 안 들어온 것"
 *  둘 다 **귀속 가능한(단일SKU) 발주**에서만 산출된다. 근거 없는 행은 0이 아니라 '모름'이다. */
type SkuSortKey = "default" | "sku" | "name" | "undelivered" | "undelivered_settled";

/** 정렬 대상 수치를 꺼낸다. null = 산출 근거 없음(0이 아님) → 방향과 무관하게 항상 뒤로 보낸다. */
function undeliveredOf(r: ReconSkuRow, settledOnly: boolean): number | null {
  return settledOnly ? r.drift_qty_settled_stage : r.drift_qty;
}

/** 한글 검색은 정규화가 갈리면 **에러 없이 0건**이 된다(교훈 #140: macOS NFD vs API NFC).
 *  화면 입력(NFC일 수도 NFD일 수도)과 API 문자열 양쪽을 NFC로 모아 비교한다. */
const norm = (s: string) => s.normalize("NFC").toLowerCase();

/** SKU 번호는 숫자 문자열이라 문자열 정렬하면 9 > 76350896이 된다 — 숫자면 숫자로 비교. */
function compareSku(a: string, b: string): number {
  const na = Number(a), nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
  return a.localeCompare(b, "ko");
}

function SkuTable({ data, from, to }: { data: RocketRecon; from: string; to: string }) {
  const [openSku, setOpenSku] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SkuSortKey>("default");
  const [asc, setAsc] = useState(true);
  const f = data.filters;

  // 검색: 공백으로 나눈 토큰 **전부**가 SKU 번호 또는 상품명에 있으면 통과(부분·순서무관).
  const shown = useMemo(() => {
    const tokens = norm(query).split(/\s+/).filter(Boolean);
    const filtered = tokens.length === 0
      ? data.skus
      : data.skus.filter((r) => {
          const hay = `${norm(r.product_number)} ${norm(r.product_name ?? "")}`;
          return tokens.every((t) => hay.includes(t));
        });
    if (sortKey === "default") return asc ? filtered : [...filtered].reverse();
    // ★방향은 비교 안에서 적용한다 — 정렬 후 통째로 뒤집으면 '모름' 행이 맨 위로 올라와,
    //   값이 없다는 이유만으로 가장 심각한 상품처럼 보인다.
    const dir = asc ? 1 : -1;
    return [...filtered].sort((a, b) => {
      if (sortKey === "sku") return dir * compareSku(a.product_number, b.product_number);
      if (sortKey === "name") {
        // 이름 없는 행은 방향과 무관하게 항상 뒤로 — '이름 없음'은 값이 아니다.
        if (a.product_name == null || b.product_name == null) {
          return (a.product_name == null ? 1 : 0) - (b.product_name == null ? 1 : 0);
        }
        return dir * a.product_name.localeCompare(b.product_name, "ko");
      }
      const settledOnly = sortKey === "undelivered_settled";
      const av = undeliveredOf(a, settledOnly);
      const bv = undeliveredOf(b, settledOnly);
      if (av == null || bv == null) return (av == null ? 1 : 0) - (bv == null ? 1 : 0);
      return dir * (av - bv);
    });
  }, [data.skus, query, sortKey, asc]);

  const searching = query.trim().length > 0;
  const byUndelivered = sortKey === "undelivered" || sortKey === "undelivered_settled";
  // 정렬 근거가 없는 행 수 — 숨기지 않고 세어서 보여준다(뒤에 몰린 이유를 알 수 있게).
  const unknownCount = byUndelivered
    ? shown.filter((r) => undeliveredOf(r, sortKey === "undelivered_settled") == null).length
    : 0;
  const baseTitle =
    f.drift_only || f.unconfirmed_only
      ? `상품별 대사 — ${n(f.sku_count_shown)}/${n(f.sku_count_total)} SKU (필터 적용)`
      : `상품별 대사 — ${n(f.sku_count_total)} SKU`;
  const title = searching ? `${baseTitle} · 검색 ${n(shown.length)}건` : baseTitle;

  const controls = (
    <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 px-4 py-3">
      <div className="relative">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="상품명 또는 SKU 번호로 검색"
          aria-label="상품명 또는 SKU 번호로 검색"
          className="w-72 rounded border border-gray-300 px-2 py-1 text-sm"
        />
      </div>
      {searching && (
        <Button variant="ghost" onClick={() => setQuery("")}>지우기</Button>
      )}
      <div className="ml-auto flex items-center gap-2">
        <label className="text-xs text-gray-500" htmlFor="sku-sort">정렬</label>
        <select
          id="sku-sort"
          value={sortKey}
          onChange={(e) => {
            const k = e.target.value as SkuSortKey;
            setSortKey(k);
            // 수량 정렬은 '많은 순'이 기본 — 미입고를 고르는 이유는 큰 것부터 보기 위해서다.
            setAsc(k === "sku" || k === "name");
          }}
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        >
          <option value="default">기본(발주 금액 큰 순)</option>
          <option value="sku">SKU 번호</option>
          <option value="name">상품명</option>
          {/* ★라벨은 '어떻게 계산했는지'가 아니라 '무엇을 세는지'로 쓴다 —
              앞 버전('발주−입고, 입고 전 포함' / '입고 끝난 단계만')은 Jino가 헷갈렸다. */}
          <option value="undelivered">아직 안 온 수량 (배송 대기 포함 — 정상)</option>
          <option value="undelivered_settled">덜 온 수량 (입고 끝났는데 부족 — 문제)</option>
        </select>
        {/* 버튼 글자는 '지금 적용된 순서' — 누르면 반대로 뒤집힌다. */}
        <Button onClick={() => setAsc(!asc)} title="정렬 방향 뒤집기">
          {sortKey === "default"
            ? (asc ? "금액 큰 순 ↓" : "금액 작은 순 ↑")
            : sortKey === "sku" || sortKey === "name"
              ? (asc ? "오름차순 ↑" : "내림차순 ↓")
              : (asc ? "적은 순 ↑" : "많은 순 ↓")}
        </Button>
      </div>
      {byUndelivered && (
        <p className="w-full text-xs text-gray-400">
          {sortKey === "undelivered_settled" ? (
            <>
              <b>덜 온 수량</b> = 입고·명세서가 <b>끝난</b> 발주(거래명세서확인·확인요청)인데 발주보다 적게 들어온 수량.
              들어왔어야 하는데 안 들어온 것이라 <b>이쪽이 진짜 문제</b>입니다. 화면의 &lsquo;발주−입고&rsquo; 열에서 <b>붉은 숫자</b>와 같은 값입니다.
            </>
          ) : (
            <>
              <b>아직 안 온 수량</b> = 발주했는데 아직 안 들어온 것 <b>전부</b>. 어제 발주해서 배송 전인 것도 포함되므로
              값이 크다고 문제가 아닙니다. &lsquo;발주−입고&rsquo; 열의 <b>붉은 숫자 + 회색 &lsquo;입고 전&rsquo;</b>을 더한 값입니다.
            </>
          )}
          {" "}두 값 모두 <b>귀속 가능한(단일 상품) 발주</b>에서만 산출되므로, 근거가 없는 {n(unknownCount)}건은
          0이 아니라 &lsquo;{NO_DATA}(모름)&rsquo;이며 <b>정렬 방향과 무관하게 항상 맨 뒤</b>에 둡니다.
        </p>
      )}
    </div>
  );

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
      {controls}
      {shown.length === 0 ? (
        <EmptyState
          reason={`"${query.trim()}"과(와) 일치하는 상품이 없습니다 (이 기간 ${n(data.skus.length)} SKU 중 0건).`}
          hint="SKU 번호 일부나 상품명 일부로 검색해 보세요. 검색은 이 기간에 조회된 상품 안에서만 이뤄집니다 — 기간 밖 상품은 기간을 넓혀야 나옵니다."
        />
      ) : (
      <Table
        head={
          <>
            {/* 머리글은 줄바꿈 금지 — '발주−입고'가 '발주−입 / 고'로 갈리면 열 이름을 못 읽는다. */}
            <Th>상품(SKU)</Th>
            <Th right><span className="whitespace-nowrap">발주</span></Th>
            <Th right><span className="whitespace-nowrap">납품가능</span></Th>
            {/* ★「보낸 수량」의 제자리는 납품가능과 입고 사이다 — 발주 → 납품가능 → 보냄 → 입고로
                줄어드는 깔때기를 그대로 읽게 한다. 「보냄 > 입고」가 미수금 후보이고, 그 구간이
                지금까지 화면에 아예 없었다(ref 45: 2026-08-05 실측 8,033,970원). */}
            <Th right><span className="whitespace-nowrap">보낸 수량</span></Th>
            <Th right><span className="whitespace-nowrap">입고</span></Th>
            <Th right><span className="whitespace-nowrap">발주−입고</span></Th>
            <Th right><span className="whitespace-nowrap">발주 금액</span></Th>
            <Th>계산서</Th>
            <Th> </Th>
          </>
        }
      >
        {shown.map((r) => (
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
      )}
      <div className="px-4 py-3 text-xs text-gray-400 space-y-1">
        <p>
          <b>입고</b> 열은 <b>단일 상품 발주에서만</b> 채워집니다 — 여러 상품이 섞인 발주는 입고수량이
          발주 단위로만 와서 상품별로 나눌 근거가 없기 때문입니다(&ldquo;{NO_DATA}&rdquo;는 0이 아니라
          &lsquo;모름&rsquo;). 각 상품의 발주 단위 실제 입고는 행을 펼쳐 확인하세요.
        </p>
        <p>
          <b>발주−입고</b>는 <b>입고가 끝난 단계</b>(거래명세서확인·확인요청)의 귀속분만 붉게 표시합니다.
          입고 전 단계의 차이는 당연하므로 회색 &lsquo;입고 전&rsquo;으로만 적습니다.
          {f.sku_count_unknown_drift > 0 && (
            <> 판정 근거가 아예 없는 상품이 {n(f.sku_count_unknown_drift)}건 있으며, &lsquo;발주≠입고만&rsquo;
            필터를 켜면 <b>정상이라서가 아니라 모르기 때문에</b> 빠집니다.</>
          )}
        </p>
        <p>
          <b>검색·정렬</b>은 위 조회 조건으로 <b>이미 불러온 상품 안에서만</b> 적용됩니다(요약 타일·수집률은
          검색과 무관하게 기간 전체 기준). 찾는 상품이 안 보이면 기간을 넓히거나 상단 필터를 해제하세요.
        </p>
        <p>
          <b>계산서</b> 배지는 <b>그 상품이 속한 발주</b> 기준입니다 — 계산서 1건이 여러 상품에 걸치므로
          행 배지를 모두 더하면 위 요약 타일보다 큽니다(요약은 기간 전체 중복 제거). 둘은 분모가 다릅니다.
        </p>
      </div>
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
          {/* ★상품명은 자르지 않는다 — 'Z플립8'과 'Z폴드8'처럼 **끝에서 갈리는** 이름이 많아
              말줄임(…)이 되면 서로 다른 상품이 같은 줄로 보인다. 폭만 제한하고 줄바꿈시킨다. */}
          <button type="button" onClick={onToggle} className="block w-full text-left">
            <span className="font-medium text-gray-900">{row.product_number}</span>
            <span className="ml-2 text-xs text-gray-500">발주 {n(row.po_count)}건</span>
            <div className="max-w-md text-xs text-gray-500 whitespace-normal break-words">
              {row.product_name ?? NO_DATA}
            </div>
          </button>
        </Td>
        <Td right>{n(row.order_qty)}</Td>
        <Td right>
          {row.confirmed_missing_lines > 0 && row.confirmed_qty === 0 ? (
            NO_DATA
          ) : row.confirmed_missing_lines > 0 ? (
            // 일부 라인만 수집된 부분합 — 완전한 수로 오독되지 않게 '≥'로 하한임을 표시.
            <span title="일부 라인이 미수집이라 이 값은 하한입니다">≥ {n(row.confirmed_qty)}</span>
          ) : (
            n(row.confirmed_qty)
          )}
          {row.confirmed_missing_lines > 0 && (
            <div className="text-xs text-gray-400">미수집 {n(row.confirmed_missing_lines)}줄</div>
          )}
        </Td>
        <Td right>
          {/* 발송 기록이 없으면 '0개 보냈다'가 아니라 **모름**이다(수집 이전 기간·트럭 쉽먼트).
              0으로 칠하면 미수집 구간이 통째로 미수금처럼 보인다. */}
          {row.shipped_qty == null ? (
            <span className="text-gray-400" title="이 상품의 발주에 발송(ASN) 기록이 아직 없습니다 (0이 아니라 '모름')">
              {NO_DATA}
            </span>
          ) : (
            <>
              {n(row.shipped_qty)}
              {row.unreceived_shipped_qty != null && row.unreceived_shipped_qty > 0 && (
                <div
                  className="text-xs text-red-600"
                  title={
                    "보냈는데 발송(ASN) 화면의 입고수량에 안 잡힌 수량 — 미수금 '후보'이지 확정이 아닙니다.\n" +
                    "① 아직 배송·입고 처리 중일 수 있습니다(발송 직후 건은 정상적으로 0입니다).\n" +
                    "② ★ASN 화면의 입고수량은 재발송분 입고를 못 봅니다 — 이 값만 믿고 청구하면 과대계상됩니다" +
                    "(2026-08-05 실측: 그렇게 해서 5,763,290원을 과대계상했습니다).\n" +
                    "확정 판정은 쿠팡 입고 원장(/scm/receive/detail/download)과 대조해야 합니다."
                  }
                >
                  미입고 {n(row.unreceived_shipped_qty)}
                  <span className="text-gray-400"> (후보)</span>
                </div>
              )}
            </>
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
          {/* 빨강은 '입고가 끝난 단계(CI·RI)'의 불일치에만 — 입고 전 단계의 차이는 당연하므로 회색. */}
          {row.drift_qty_settled_stage == null ? (
            <span className="text-gray-400" title="입고가 끝난 단계의 귀속 가능 발주가 없어 판정할 수 없습니다 (0이 아니라 '모름')">
              {NO_DATA}
            </span>
          ) : row.drift_qty_settled_stage === 0 ? (
            <span className="text-gray-400">0</span>
          ) : (
            <span className="text-judge-bad font-medium">{n(row.drift_qty_settled_stage)}</span>
          )}
          {/* ★수량이 상쇄돼 0이어도 불일치 발주가 있으면 그 사실을 지운 채 두지 않는다. */}
          {row.drift_qty_settled_stage === 0 && row.drift_po_count_settled_stage > 0 && (
            <div className="text-xs text-judge-bad" title="초과입고와 미입고가 섞여 수량 합이 0이 됐을 뿐, 발주별로는 불일치합니다">
              발주 {n(row.drift_po_count_settled_stage)}건 불일치(상쇄)
            </div>
          )}
          {/* 입고 전 단계의 차이 — null 분기 안에 가두면 settled=0일 때 정보가 사라진다(R2-2). */}
          {row.drift_qty != null && row.drift_qty !== row.drift_qty_settled_stage && (
            <div className="text-xs text-gray-400" title="아직 입고 전 단계라 발주≠입고가 정상입니다">
              입고 전 {n(row.drift_qty - (row.drift_qty_settled_stage ?? 0))}
            </div>
          )}
        </Td>
        {/* 금액은 '36,430,080 / 원'으로 갈리면 자릿수를 잘못 읽는다 — 한 덩어리로 묶는다. */}
        <Td right><span className="whitespace-nowrap">{won(row.order_amount)}</span></Td>
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
          <span className="whitespace-nowrap">
            <Button variant="ghost" onClick={onToggle}>{open ? "닫기 ▲" : "발주내역 ▼"}</Button>
          </span>
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
