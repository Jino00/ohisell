// KpiEvidence.tsx — 대시보드 KPI 카드 4칸의 «근거» 페이지
//
// 계약: docs/contracts/CONTRACT_kpi_evidence_page.md (Jino 승인 2026-08-23)
// Jino 원문: "거기를 누르면 근거자료(계산페이지)가 나오게 만들자. 근거자료가 있으면 더 좋잖아"
//
// ★이 화면이 지키는 것 셋 — 어기면 근거가 아니라 두 번째 거짓말이 된다.
//   ① 숫자는 카드와 **같은 경로**에서 온다(`/api/dashboard/kpi/evidence`가 `_channel_rows` →
//      `_kpi_totals`를 그대로 쓴다). 여기서 다시 계산하지 않는다.
//   ② `null`은 **0원이 아니라 「모른다」**다 — "—"로 그린다. 0으로 그리면 「원가 0원」 거짓말.
//   ③ 검산 ✓/✗는 화면이 **스스로** 찍는다. 사람 눈에 맡기면 안 맞는 날 아무도 모른다.
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  fetchKpiEvidence,
  type KpiEvidence as KpiEvidenceData,
  type KpiEvidenceRow,
  type KpiMetric,
  type RocketBasis,
} from "../lib/api";

export const METRIC_TITLES: Record<KpiMetric, string> = {
  revenue: "총 매출",
  net_profit: "순이익",
  profit_rate: "이익률",
  order_count: "주문 건수",
};

/** 순이익 검산식에서 빼는 항목의 한국어 이름. 백엔드 `deduction_keys`와 키가 같아야 한다. */
export const DEDUCTION_LABELS: Record<string, string> = {
  cost: "원가",
  commission: "수수료",
  ad_spend: "광고비",
  shipping: "판매자 배송비",
  fixed_cost: "고정비(일할)",
  promo_burden: "프로모션 분담금",
  payable_vat: "납부 부가세",
};

const METRIC_ORDER: KpiMetric[] = ["revenue", "net_profit", "profit_rate", "order_count"];

export function isKpiMetric(value: string | null): value is KpiMetric {
  return value != null && (METRIC_ORDER as string[]).includes(value);
}

/** 금액 문자열 → 화면 글자. **null은 "—"** (0원이 아니라 「모른다」). */
export function won(value: string | null | undefined): string {
  if (value == null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${Math.round(n).toLocaleString("ko-KR")}원`;
}

function num(value: string | null | undefined): number {
  return value == null ? 0 : Number(value);
}

/** 검산 결과 배지 — 화면이 스스로 찍는 ✓/✗. */
export function CheckBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium ${
        ok ? "text-green-700" : "text-red-700"
      }`}
    >
      {ok ? "✓" : "✗"} {label}
    </span>
  );
}

function Amount({ value, bold }: { value: string | null | undefined; bold?: boolean }) {
  const unknown = value == null;
  return (
    <span
      className={`${bold ? "font-semibold" : ""} ${unknown ? "text-gray-400" : ""}`}
      title={unknown ? "이 채널 행은 이 항목을 싣지 않는다 — 0원이 아니라 «모른다»" : undefined}
    >
      {won(value)}
    </span>
  );
}

// ── 총 매출 ────────────────────────────────────────────────────────────────
export function RevenueEvidence({ data }: { data: KpiEvidenceData }) {
  return (
    <>
      <Equation
        title="채널별 매출의 합이 카드 숫자다"
        lines={data.rows.map((r) => ({ label: r.label || r.channel_name, value: r.revenue }))}
        total={data.totals.revenue}
        totalLabel="총 매출"
        ok={data.checks.revenue_matches}
      />
      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-y">
          <tr>
            <Th align="left">채널</Th>
            <Th>제품 매출</Th>
            <Th>배송비 매출</Th>
            <Th>매출</Th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {data.rows.map((r) => (
            <tr key={rowKey(r)}>
              <TdLabel row={r} />
              <Td><Amount value={r.product_revenue} /></Td>
              <Td><Amount value={r.shipping_revenue} /></Td>
              <Td><Amount value={r.revenue} bold /></Td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

// ── 순이익 ─────────────────────────────────────────────────────────────────
export function NetProfitEvidence({ data }: { data: KpiEvidenceData }) {
  const residual = num(data.totals.residual);
  return (
    <>
      <Equation
        title="총매출에서 비용을 빼면 순이익이다"
        first={{ label: "총 매출", value: data.totals.revenue }}
        lines={data.deduction_keys
          .filter((k) => num(data.deduction_totals[k]) !== 0 || data.deduction_unknown_rows[k] > 0)
          .map((k) => ({
            label: `− ${DEDUCTION_LABELS[k] ?? k}`,
            value: data.deduction_totals[k],
            note:
              data.deduction_unknown_rows[k] > 0
                ? `채널 ${data.deduction_unknown_rows[k]}곳은 이 항목을 안 싣는다`
                : undefined,
          }))}
        residual={residual !== 0 ? data.totals.residual : undefined}
        total={data.totals.net_profit}
        totalLabel="= 순이익"
        ok={data.checks.net_matches && data.checks.net_fully_explained}
      />

      {data.has_floor && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          ⚠️ <b>이 순이익은 하한입니다</b> — 손익을 못 재는 채널(로켓배송 계산서 축 등)은 원가를
          몰라 <b>확정 비용인 광고비 {won(data.totals.floor_ad)}만</b> 비용으로 반영됐습니다.
          실제 손익은 이 값보다 <b>낮습니다</b>.
        </div>
      )}

      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-y">
          <tr>
            <Th align="left">채널</Th>
            <Th>매출</Th>
            {data.deduction_keys.map((k) => (
              <Th key={k}>{DEDUCTION_LABELS[k] ?? k}</Th>
            ))}
            <Th>순이익</Th>
            <Th>검산</Th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {data.rows.map((r) => (
            <tr key={rowKey(r)}>
              <TdLabel row={r} />
              <Td><Amount value={r.revenue} /></Td>
              {data.deduction_keys.map((k) => (
                <Td key={k}><Amount value={r.deductions[k]} /></Td>
              ))}
              <Td>
                <Amount value={r.net_profit} bold />
                {r.net_scope === "ad_only" && (
                  <span className="ml-1 text-xs text-amber-700" title="광고비만 반영된 하한">
                    하한
                  </span>
                )}
              </Td>
              <Td>
                {r.explains_net ? (
                  <span className="text-green-700">✓</span>
                ) : (
                  <span className="text-amber-700" title={residualTitle(r)}>
                    {r.residual == null ? "—" : `잔차 ${won(r.residual)}`}
                  </span>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function residualTitle(row: KpiEvidenceRow): string {
  if (row.missing.length > 0) {
    const names = row.missing.map((k) => DEDUCTION_LABELS[k] ?? k).join("·");
    return `이 행이 안 싣는 항목: ${names}`;
  }
  return "항목 합과 순이익의 차액";
}

// ── 이익률 ─────────────────────────────────────────────────────────────────
export function ProfitRateEvidence({ data }: { data: KpiEvidenceData }) {
  const unmeasured = num(data.totals.unmeasured_revenue);
  return (
    <>
      <div className="mb-6 rounded-lg border bg-white p-4">
        <div className="text-sm text-gray-500 mb-2">이익률 = 순이익 ÷ 손익을 잰 매출</div>
        <div className="font-mono text-sm space-y-1">
          <div>분자 · 순이익 &nbsp; <b>{won(data.totals.net_profit)}</b></div>
          <div>분모 · 손익을 잰 매출 &nbsp; <b>{won(data.totals.basis_revenue)}</b></div>
          <div className="pt-1 border-t">
            = <b className="text-base">{Number(data.totals.profit_rate).toFixed(1)}%</b>
          </div>
        </div>
      </div>

      {unmeasured !== 0 && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          ⚠️ <b>분모는 총 매출이 아닙니다</b> — 총 매출 {won(data.totals.revenue)} 중{" "}
          <b>{won(data.totals.unmeasured_revenue)}</b>은 <b>원가를 몰라 손익을 못 잰 매출</b>이라
          분모에서 빠졌습니다. 그 매출을 분모에 넣으면 「하한 손익 ÷ 원가 미상 매출」이라는 뜻 없는
          비율이 됩니다.
        </div>
      )}

      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-y">
          <tr>
            <Th align="left">채널</Th>
            <Th>매출</Th>
            <Th>손익을 잰 매출(분모)</Th>
            <Th>순이익(분자)</Th>
            <Th>원가 미상 매출</Th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {data.rows.map((r) => (
            <tr key={rowKey(r)}>
              <TdLabel row={r} />
              <Td><Amount value={r.revenue} /></Td>
              <Td><Amount value={r.net_basis_revenue} /></Td>
              <Td><Amount value={r.net_profit} /></Td>
              <Td><Amount value={r.unmapped_revenue} /></Td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

// ── 주문 건수 ──────────────────────────────────────────────────────────────
export function OrderCountEvidence({ data }: { data: KpiEvidenceData }) {
  const excluded = data.rows.filter((r) => !r.counted_in_order_card);
  return (
    <>
      <Equation
        title="카드에 세는 채널의 건수 합이 카드 숫자다"
        lines={data.rows
          .filter((r) => r.counted_in_order_card)
          .map((r) => ({
            label: r.label || r.channel_name,
            value: String(r.order_count),
            unit: "건",
          }))}
        total={String(data.totals.order_count)}
        totalLabel="주문 건수"
        unit="건"
        ok={data.checks.order_count_matches}
      />

      {excluded.length > 0 && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          ⚠️ <b>로켓배송 1P는 이 카드에서 빠집니다</b> — 매입 구조라 <b>주문이라는 개념이 없고</b>,
          그 칸에 담긴 {data.order_count_excluded.toLocaleString("ko-KR")}은 주문 건수가 아니라{" "}
          <b>판매수량</b>입니다. 더하면 「주문 건수」가 매출 축에 따라 뜻이 바뀝니다.
          <div className="mt-1 text-xs text-amber-700">
            제외된 채널: {excluded.map((r) => r.label || r.channel_name).join(", ")}
          </div>
        </div>
      )}
    </>
  );
}

// ── 공통 조각 ──────────────────────────────────────────────────────────────
type EqLine = { label: string; value: string | null; note?: string; unit?: string };

function Equation({
  title,
  first,
  lines,
  residual,
  total,
  totalLabel,
  unit,
  ok,
}: {
  title: string;
  first?: EqLine;
  lines: EqLine[];
  residual?: string;
  total: string;
  totalLabel: string;
  unit?: string;
  ok: boolean;
}) {
  const fmt = (v: string | null, u?: string) =>
    u === "건" ? `${Number(v ?? 0).toLocaleString("ko-KR")}건` : won(v);
  return (
    <div className="mb-6 rounded-lg border bg-white p-4">
      <div className="text-sm text-gray-500 mb-3">{title}</div>
      <div className="font-mono text-sm space-y-1">
        {first && (
          <Row label={first.label} value={fmt(first.value, first.unit ?? unit)} />
        )}
        {lines.map((l) => (
          <Row
            key={l.label}
            label={l.label}
            value={fmt(l.value, l.unit ?? unit)}
            note={l.note}
          />
        ))}
        {residual !== undefined && (
          <Row
            label="− 설명 못 한 잔차"
            value={won(residual)}
            note="항목 합으로 재현되지 않는 금액 — 숨기지 않고 이름을 붙인다"
            tone="text-amber-700"
          />
        )}
        <div className="flex justify-between pt-2 border-t items-center">
          <span className="font-semibold">{totalLabel}</span>
          <span className="flex items-center gap-3">
            <b className="text-base">{fmt(total, unit)}</b>
            <CheckBadge ok={ok} label={ok ? "카드와 일치" : "카드와 불일치"} />
          </span>
        </div>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: string;
}) {
  return (
    <div className={`flex justify-between gap-4 ${tone ?? ""}`}>
      <span className="text-gray-700">
        {label}
        {note && <span className="ml-2 text-xs text-gray-400">{note}</span>}
      </span>
      <span>{value}</span>
    </div>
  );
}

function Th({ children, align }: { children: React.ReactNode; align?: "left" }) {
  return (
    <th
      className={`px-3 py-2 text-xs font-medium text-gray-500 ${
        align === "left" ? "text-left" : "text-right"
      }`}
    >
      {children}
    </th>
  );
}

function Td({ children }: { children: React.ReactNode }) {
  return <td className="px-3 py-2 text-right whitespace-nowrap">{children}</td>;
}

function TdLabel({ row }: { row: KpiEvidenceRow }) {
  return (
    <td className="px-3 py-2 text-left">
      {row.label || row.channel_name}
      {row.revenue_basis && (
        <span className="ml-1 text-xs text-gray-400">· {row.revenue_basis}</span>
      )}
    </td>
  );
}

function rowKey(r: KpiEvidenceRow): string {
  return String(r.channel_id ?? r.channel_name);
}

export function EvidenceBody({
  metric,
  data,
}: {
  metric: KpiMetric;
  data: KpiEvidenceData;
}) {
  if (metric === "revenue") return <RevenueEvidence data={data} />;
  if (metric === "net_profit") return <NetProfitEvidence data={data} />;
  if (metric === "profit_rate") return <ProfitRateEvidence data={data} />;
  return <OrderCountEvidence data={data} />;
}

// ── 페이지 ─────────────────────────────────────────────────────────────────
export default function KpiEvidencePage() {
  const [params, setParams] = useSearchParams();
  const metricParam = params.get("metric");
  const metric: KpiMetric = isKpiMetric(metricParam) ? metricParam : "revenue";
  const dateFrom = params.get("date_from") ?? "";
  const dateTo = params.get("date_to") ?? "";
  const rocketBasis = (params.get("rocket_basis") ?? "settlement") as RocketBasis;

  const [data, setData] = useState<KpiEvidenceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchKpiEvidence(dateFrom, dateTo, rocketBasis));
    } catch (e) {
      // ★조용히 빈 화면을 내지 않는다 — 근거 페이지가 아무 말도 안 하면 「근거가 없다」로 읽힌다.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, rocketBasis]);

  useEffect(() => {
    void load();
  }, [load]);

  // 대시보드로 돌아갈 때 **조회 조건을 그대로 들고 간다** — 안 그러면 뒤로 가기가 기간을 잃는다.
  const backTo =
    `/?date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}` +
    `&rocket_basis=${encodeURIComponent(rocketBasis)}`;

  return (
    <div className="p-6 max-w-6xl">
      <Link to={backTo} className="text-sm text-blue-600 hover:underline">
        ← 대시보드로 돌아가기
      </Link>

      <h1 className="text-2xl font-bold mt-2 mb-1">
        {METRIC_TITLES[metric]} — 근거
      </h1>
      <div className="text-sm text-gray-500 mb-4">
        조회 기간 {dateFrom || "—"} ~ {dateTo || "—"} · 로켓 축{" "}
        {rocketBasis === "sales" ? "판매(납품가)" : "계산서"}
      </div>

      <div className="flex gap-1 mb-6 bg-gray-100 rounded-lg p-0.5 w-fit">
        {METRIC_ORDER.map((m) => (
          <button
            key={m}
            onClick={() => {
              const next = new URLSearchParams(params);
              next.set("metric", m);
              setParams(next, { replace: true });
            }}
            className={`px-3 py-1.5 text-sm rounded-md ${
              m === metric ? "bg-white shadow text-blue-700 font-medium" : "text-gray-600"
            }`}
          >
            {METRIC_TITLES[m]}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          근거를 불러오지 못했습니다 — {error}
        </div>
      )}
      {loading && !data && <div className="text-sm text-gray-500">불러오는 중…</div>}
      {data && (
        <>
          <EvidenceBody metric={metric} data={data} />
          <div className="mt-6 text-xs text-gray-500 space-y-1">
            <div>
              <b>"—"는 0원이 아니라 «모른다»입니다.</b> 로켓배송 1P·로켓그로스(RG) 행은 다른 엔진에서
              오기 때문에 분해 항목을 전부 싣지 않습니다. 0으로 채우면 「원가 0원」이라는 거짓말이
              됩니다.
            </div>
            <div>
              이 페이지의 숫자는 대시보드 카드와 <b>같은 계산 경로</b>에서 나옵니다 — 근거용으로
              다시 계산하지 않습니다.
            </div>
          </div>
        </>
      )}
    </div>
  );
}
