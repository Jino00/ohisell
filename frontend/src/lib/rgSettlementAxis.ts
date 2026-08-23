// rgSettlementAxis.ts — RG 정산공제가 «어느 축이고 무엇을 근거로 하는가»를 화면이 말하게 한다.
// (계약 CONTRACT_rg_sales_date_axis §4 ⓑⓒⓓⓔ, 2026-08-22)
//
// 왜 순수 함수로 빼나: 같은 저장소에서 이미 겪었다 — `Dashboard.tsx`의 분기를 통째로 지워도
// 프론트 테스트 445개가 전부 초록이었다(그 파일을 참조하는 테스트가 0개, `netScope.ts` 머리말).
// 판정을 순수 함수로 빼면 테스트가 붙고, 붙으면 다음에 누가 지울 때 깨진다.
//
// ★이 파일이 없으면 무엇이 안 보이나: 실측 요율과 「못 잼」이 **화면에서 같은 얼굴**을 한다.
//   3P는 이미 `basis="default_rate"`로 이 구분을 화면에 싣는다. 2P도 같은 대접을 받아야 한다.
import type { RowNote } from "./netScope";

const won = (n: number) => `${Math.round(n).toLocaleString("ko-KR")}원`;
const num = (v: unknown): number => Number(v ?? 0) || 0;

/** 실측 요율로 잰 축인가, 못 재서 옛 축(정산 인식일 통짜)으로 물러섰나. */
export const AXIS_SALES_DATE = "sales_date";
export const BASIS_SETTLED = "settled_rate";
export const BASIS_RATE_UNKNOWN = "rate_unknown";

/** 장부 대조 차이가 이 %를 넘으면 ⚠️를 단다. 넘어도 **숨기지 않는다** — 표시하고 표시했다고 말한다. */
export const RECONCILE_WARN_PCT = 5;

/** 두 화면(대시보드 leaf 행 · 종합조망 계정 요약)이 서로 다른 키로 같은 사실을 준다 — 여기서 합친다. */
export interface RgFeeFacts {
  axis?: string | null;          // sales_date | recognition_date
  basis?: string | null;         // settled_rate | rate_unknown
  rate?: string | number | null; // 판매수수료 요율 %(VAT 포함)
  cycles?: string | null;        // 그 요율을 잰 완결 정산주기 범위
  coverage?: string | number | null;        // 0~1. 물류비 단가를 아는 매출의 비율
  unmappedRevenue?: string | number | null; // 단가를 몰라 0으로 «안 채운» 매출
  reconcileCycle?: string | null;
  reconcileActual?: string | number | null;
  reconcileDiff?: string | number | null;
  reconcilePct?: string | number | null;
}

/**
 * 정산공제 칸 아래에 붙는 자백. 없으면(=RG 행이 아니면) null.
 *
 * 세 가지를 말한다:
 *   ① 축      — 그날 판 것에 붙는 비용인가(sales_date), 아니면 주간 통짜인가(recognition_date)
 *   ② 근거    — 요율이 실측인가 못 잰 것인가 + 어느 완결 주기에서 쟀나
 *   ③ 덮은 폭 — 단가를 아는 매출 비율과, 몰라서 «안 채운» 매출
 * 그리고 장부 총액 대조(§4 ⓒ)를 **수치 그대로** 붙인다 — 차이를 0으로 만들지 않는다.
 */
export function rgFeeNote(f: RgFeeFacts): RowNote | null {
  if (!f.axis && !f.basis) return null;

  const parts: string[] = [];
  const why: string[] = [];

  if (f.axis === AXIS_SALES_DATE) {
    parts.push("판매일 축");
    why.push("그날 판 수량·매출에 붙는 비용만 센다(물류비=수량×단가, 수수료=매출×요율).");
  } else {
    parts.push("⚠️ 정산 인식일 축");
    why.push(
      "판매일 축으로 못 냈다 — 요율을 못 재거나 물류비 단가 커버리지가 얇다. " +
        "이 값은 정산 주기 통짜라 그 주기를 덮는 어느 하루를 물어도 같다.",
    );
  }

  if (f.basis === BASIS_RATE_UNKNOWN) {
    parts.push("요율 미상");
    why.push(
      "판매수수료 요율을 잴 완결 정산주기가 없다. 기본 요율로 추정하지 않는다 — " +
        "RG엔 그런 근거값이 없다(계약 §8-4).",
    );
  } else if (f.rate != null) {
    const cycles = f.cycles ? `, ${f.cycles}` : "";
    parts.push(`요율 ${num(f.rate).toFixed(2)}%(실측${cycles})`);
    why.push("요율은 최근 완결 정산주기의 실측값이다 — 상수가 아니라 주기마다 갱신된다.");
  }

  const cov = f.coverage == null ? null : num(f.coverage);
  if (cov != null && cov < 1) {
    parts.push(`비용 커버리지 ${(cov * 100).toFixed(1)}%`);
    const un = num(f.unmappedRevenue);
    if (un > 0) {
      // ★사유를 두 갈래로 적는다(적대 리뷰 2R P2): 이 금액엔 ①단가를 모르는 옵션과
      //   ②옵션축에 아예 없는 매출(요약축에만 있는 날)이 **함께** 들어온다. 종전 문구는
      //   ①만 말해서 금액은 맞는데 사유가 틀렸다.
      why.push(
        `매출 ${won(un)}에는 이 방식이 비용을 못 붙였다(단가를 모르는 옵션 또는 옵션축이 비어 있는 날) — ` +
          "그 몫을 0으로 «채우지 않았다». 그만큼 이 공제는 하한이다.",
      );
    }
  }

  const pct = f.reconcilePct == null ? null : num(f.reconcilePct);
  if (f.reconcileCycle) {
    const diff = num(f.reconcileDiff);
    const sign = diff >= 0 ? "+" : "−";
    const pctText = pct == null ? "" : `(${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)`;
    const warn = pct != null && Math.abs(pct) > RECONCILE_WARN_PCT ? "⚠️ " : "";
    parts.push(`${warn}장부대조 ${sign}${won(Math.abs(diff))}${pctText}`);
    why.push(
      `완결 주기 ${f.reconcileCycle}에서 이 방식의 합과 원장 실청구액 ${won(num(f.reconcileActual))}의 차이다. ` +
        "0으로 맞추지 않고 그대로 보인다.",
    );
  }

  return { text: parts.join(" · "), title: why.join(" ") };
}

/** 대시보드 채널 요약 행(`GroupedSummaryRow`)의 칸 이름 → 공통 사실. */
export function rgFeeFactsFromRow(row: Record<string, unknown>): RgFeeFacts {
  return {
    axis: (row.commission_axis as string) ?? null,
    basis: (row.commission_basis as string) ?? null,
    rate: (row.commission_rate as string) ?? null,
    cycles: (row.commission_rate_cycles as string) ?? null,
    coverage: (row.fee_coverage as string) ?? null,
    unmappedRevenue: (row.fee_unmapped_revenue as string) ?? null,
    reconcileCycle: (row.settlement_reconcile_cycle as string) ?? null,
    reconcileActual: (row.settlement_reconcile_actual as string) ?? null,
    reconcileDiff: (row.settlement_reconcile_diff as string) ?? null,
    reconcilePct: (row.settlement_reconcile_pct as string) ?? null,
  };
}

/**
 * RG «자기 화면»(`/api/coupang/rg/option-pnl`)의 칸 이름 → 공통 사실.
 * 계약 `CONTRACT_2p_own_screens.md`(D-CPP-54) §1-B — **자백 문장을 새로 쓰지 않는다.**
 *
 * ★왜 어댑터를 하나 더 두나: 같은 사실을 세 표면이 서로 다른 키로 준다(대시보드 leaf 행 ·
 *   종합조망 계정 요약 · 이 화면). 문장을 각자 쓰면 세 화면이 같은 값을 다르게 말하게 된다 —
 *   D-CPP-47이 고쳤던 병의 문장판이다. 키만 여기서 맞추고 문장은 `rgFeeNote()` 하나가 낸다.
 * ★요율은 이 엔드포인트에서 비율(0~1)로 온다(종합조망과 같고 대시보드 행과 다르다).
 */
export function rgFeeFactsFromOptionPnl(r: Record<string, unknown>): RgFeeFacts {
  const rec = (r.reconciliation ?? null) as Record<string, unknown> | null;
  return {
    axis: (r.commission_axis as string) ?? null,
    basis: (r.rate_basis as string) ?? null,
    rate: r.rate == null ? null : num(r.rate) * 100,
    cycles: (r.rate_cycles as string) ?? null,
    coverage: (r.fee_coverage as string | number) ?? null,
    unmappedRevenue:
      ((r.account_common as Record<string, unknown> | undefined)?.fee_unmapped_revenue as
        | string
        | number
        | undefined) ?? null,
    reconcileCycle: rec ? `${rec.cycle_from}~${rec.cycle_to}` : null,
    reconcileActual: rec ? (rec.actual as string) : null,
    reconcileDiff: rec ? (rec.diff as string) : null,
    reconcilePct: rec ? (rec.diff_pct as string) : null,
  };
}

/** 종합조망 계정 요약(`account.summary`)의 칸 이름 → 공통 사실. 요율은 비율(0~1)로 온다. */
export function rgFeeFactsFromSummary(s: Record<string, unknown>): RgFeeFacts {
  const rec = (s.rg_fee_reconcile ?? null) as Record<string, unknown> | null;
  const rate = s.rg_fee_rate == null ? null : num(s.rg_fee_rate) * 100;
  return {
    axis: (s.rg_settlement_axis as string) ?? null,
    basis: (s.rg_fee_basis as string) ?? null,
    rate,
    cycles: null,
    coverage: (s.rg_fee_coverage as string | number) ?? null,
    unmappedRevenue: (s.rg_fee_unmapped_revenue as string | number) ?? null,
    reconcileCycle: rec ? `${rec.cycle_from}~${rec.cycle_to}` : null,
    reconcileActual: rec ? (rec.actual as string) : null,
    reconcileDiff: rec ? (rec.diff as string) : null,
    reconcilePct: rec ? (rec.diff_pct as string) : null,
  };
}
