// Rocket1PPnlAudit.tsx — 로켓1P 손익 «근거 화면» (2026-08-07 설계, Jino 승인)
//
// Jino 원문: "우리 손익(납품가 축)이 정말 실수 없이 나오는지 어떻게 확신할 수 있는지"
// 구조: 1단 산술 검사(A1~A7·B1~B3) → 2단 사다리(손익 화면과 같은 함수 산출) →
//      3단 원자 목록(「날짜×옵션」) → 4단 클릭 시 원천 행 5갈래.
// ★이 화면은 계산하지 않는다 — 백엔드가 화면과 같은 함수를 다른 그레인으로 불러 비교한 것을
//   그대로 보인다. 검사 verdict 3값: pass(초록)/fail(빨강)/undetermined(회색 «판정 안 함»).
//   B1(두 축 대사)은 영구 회색이다 — 1P 재고 데이터가 없어 두 축 차이를 판정할 수 없다
//   (거짓 초록 금지). B3(광고 두 축 대사)도 같은 이유로 영구 회색 — 정의가 다른 두 축을
//   임계값 지어내 pass/fail로 가르지 않는다.
import { Fragment, useState, type ReactNode } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { Card, Table, Th, Td, Loading, EmptyState, Badge } from "../components/ui";
import { useAsyncData } from "../lib/useAsyncData";
import { kstDate } from "../lib/periodRange";
import {
  fetchPnlAuditChecks, fetchPnlAuditAtoms, fetchPnlAuditAtom,
  type PnlAuditCheck, type PnlAuditAtom, type PnlAuditAtomDetail,
} from "../lib/api";

const NO_DATA = "—";
const won = (v: string | number | null | undefined) => {
  if (v == null) return NO_DATA;
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? `${Math.round(n).toLocaleString("ko-KR")}원` : NO_DATA;
};
const num = (v: number | null | undefined) => (v == null ? NO_DATA : v.toLocaleString("ko-KR"));
const pct = (v: string | null | undefined) => {
  if (v == null) return NO_DATA;
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : NO_DATA;
};

/** 판정 칩 — undetermined는 «판정 안 함»이다. 회색이지 초록이 아니다(거짓 초록 금지). */
function Verdict({ v }: { v: PnlAuditCheck["verdict"] }) {
  if (v === "pass") {
    return <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">통과</span>;
  }
  if (v === "fail") {
    return <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800">불일치</span>;
  }
  return <span className="rounded bg-gray-200 px-2 py-0.5 text-xs font-medium text-gray-600">판정 안 함</span>;
}

const SOURCE_LABEL: Record<PnlAuditAtom["cost_source"], { text: string; cls: string }> = {
  manual: { text: "수기 확인", cls: "bg-green-50 text-green-700" },
  suggested: { text: "이름 유사도 — 사람 미확인", cls: "bg-amber-100 text-amber-800" },
  unknown: { text: "확정 방법 미기록", cls: "bg-amber-50 text-amber-700" },
  excluded: { text: "원가 제외 결정", cls: "bg-gray-100 text-gray-600" },
  no_cost: { text: "원가 미등록 — 다리는 있음", cls: "bg-red-50 text-red-700" },
  no_link: { text: "다리 없음 — 연결부터", cls: "bg-red-50 text-red-700" },
};

function DetailBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded border border-gray-200 p-2">
      <div className="mb-1 text-xs font-semibold text-gray-500">{title}</div>
      <div className="text-xs leading-relaxed text-gray-800">{children}</div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-gray-400">{k}</span>
      <span className="tabular-nums">{v}</span>
    </div>
  );
}

/** 4단 — 원자 1개의 다섯 갈래 원천 행. 없는 행은 «없음»으로 그대로 말한다(0이 아니다).
 *  ★from/to(화면이 보고 있는 창)를 반드시 함께 넘긴다 — 창을 좁히면 분담금 «모름» 판정이
 *    달라져 화면이 «—»로 그린 행에 숫자가 찍힌다(원자 파생 SA의 창 종속성 계약).
 *  ★a6Pass — 창 단위 프로모션 수집 신선도는 위 1단 A6 검사가 판정한다. 이 컴포넌트는 그
 *    판정 없이는 "그날 걸린 프로모션 없음 — 분담금 0은 사실"을 **말하지 않는다**: 빈 목록은
 *    "수집이 멈춰 그날 것을 못 봤다"일 수도 있어서다(백엔드 구현자가 계획서에 남긴 경고). */
function AtomDetail({ date, optionId, from, to, a6Pass }: {
  date: string; optionId: string; from: string; to: string; a6Pass: boolean;
}) {
  const { data, error } = useAsyncData<PnlAuditAtomDetail>(
    () => fetchPnlAuditAtom({ date, optionId, from, to }), [date, optionId, from, to]);
  if (error) return <div className="p-2 text-xs text-red-600">원천 조회 실패: {String(error)}</div>;
  if (!data) return <div className="p-2"><Loading /></div>;
  const cm = data.cost.map;
  return (
    <div className="grid gap-2 bg-gray-50 p-3 md:grid-cols-5">
      <DetailBlock title="① 판매행 (판매분석)">
        {data.sales ? (<>
          <KV k="수량" v={num(data.sales.qty)} />
          <KV k="소비자 실현가" v={won(data.sales.consumer_revenue)} />
          <KV k="수집 시각" v={data.sales.synced_at ?? NO_DATA} />
        </>) : "행 없음"}
      </DetailBlock>
      <DetailBlock title="② 납품단가 (최근 발주)">
        {data.unit_price ? (<>
          <KV k="발주번호" v={String(data.unit_price.purchase_order_seq)} />
          <KV k="발주일" v={data.unit_price.po_created_at?.slice(0, 10) ?? NO_DATA} />
          <KV k="단가" v={won(data.unit_price.unit_purchase_price)} />
          <KV k="공유 옵션" v={`${data.unit_price.sibling_option_count}개`} />
          <p className="mt-1 text-[11px] text-amber-700">⚠️ {data.unit_price.note}</p>
        </>) : "발주 이력 없음 — 이 판매는 손익 매출에서 빠져 있습니다(A5)"}
      </DetailBlock>
      <DetailBlock title="③ 원가 (다리 → 등록원가)">
        {cm ? (<>
          <KV k="내부 SKU" v={cm.internal_sku ?? NO_DATA} />
          <KV k="상태" v={`${cm.status}${cm.match_method ? ` · ${cm.match_method}` : ""}`} />
          <KV k="등록원가" v={won(data.cost.master?.cost_price)} />
          {cm.note && <p className="mt-1 text-[11px] text-gray-500">{cm.note}</p>}
          {cm.match_method === "suggested" && (
            <p className="mt-1 text-[11px] font-medium text-amber-800">
              이름 유사도로 자동 확정 — 사람이 확인하지 않았습니다.{" "}
              <Link className="underline" to="/command-center">원가 매핑 화면으로</Link>
            </p>
          )}
        </>) : "다리 없음 — 원가를 등록해도 붙지 않습니다(연결부터)"}
      </DetailBlock>
      <DetailBlock title="④ 광고비 (옵션×일)">
        {data.ad ? (<>
          <KV k="광고비" v={won(data.ad.ad_spend)} />
          <KV k="노출/클릭" v={`${num(data.ad.impressions)}/${num(data.ad.clicks)}`} />
        </>) : "행 없음 — 그날 이 옵션에 광고 없음(손익에선 0원이 맞음)"}
      </DetailBlock>
      <DetailBlock title="⑤ 분담금 (프로모션 제안서)">
        {data.promos == null ? "원천 테이블 없음(모름)" : data.promos.length === 0
          ? (a6Pass
            ? "그날 걸린 프로모션 없음 — 분담금 0은 사실"
            // ★A6이 pass가 아니면 "0은 사실"을 말하지 않는다 — 수집 신선도가 창 끝을 못
            //   덮는 상태일 수 있고, 그때는 «없었다»와 «못 봤다»를 가를 수 없다.
            : "그날 걸린 프로모션 없음(이 창의 프로모션 수집 신선도는 위 A6 참조)")
          : data.promos.map((p, i) => (
            <div key={i}>
              <KV k={p.request_id} v={p.discount_value == null
                ? "할인액 모름(제안서 미수집)"
                : `${p.discount_type} ${p.discount_value}`} />
              <div className="text-[11px] text-gray-400">
                {p.start_at?.slice(0, 10)} ~ {p.end_at?.slice(0, 10)}
              </div>
            </div>
          ))}
      </DetailBlock>
    </div>
  );
}

export default function Rocket1PPnlAudit() {
  const [sp] = useSearchParams();
  const from = sp.get("from") ?? kstDate(-6);
  const to = sp.get("to") ?? kstDate(0);
  const [flt, setFlt] = useState("all");
  const [sort, setSort] = useState("revenue");
  const [open, setOpen] = useState<string | null>(null);   // `${date}|${option_id}`

  const checks = useAsyncData(() => fetchPnlAuditChecks({ from, to }), [from, to]);
  const atoms = useAsyncData(
    () => fetchPnlAuditAtoms({ from, to, sort, flt }), [from, to, sort, flt]);

  const ladder = checks.data?.ladder;
  // ★4단의 "분담금 0은 사실" 문장은 이 판정 없이는 쓸 수 없다(계약 조항 5).
  const a6Pass = checks.data?.checks.find((c) => c.id === "A6")?.verdict === "pass";

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-gray-900">로켓1P 손익 — 근거</h1>
        <p className="mt-1 text-sm text-gray-500">
          기간 {from} ~ {to} · 이 화면은 계산하지 않습니다 — 손익 화면과 같은 함수의 결과를
          다른 그레인으로 대조한 것입니다. URL을 공유하면 같은 것이 재현됩니다.
        </p>
      </div>

      {/* ── 1단: 산술 검사 ── */}
      <Card title="산술 검사">
        {checks.error ? <EmptyState reason="검사 조회 실패" hint={String(checks.error)} />
          : !checks.data ? <Loading />
          : (
            <Table head={<>
              <Th>검사</Th><Th right>좌변</Th><Th right>우변</Th><Th right>차이</Th><Th>판정</Th>
            </>}>
              {checks.data.checks.map((c) => (
                <tr key={c.id} className="align-top hover:bg-gray-50">
                  <Td>
                    <span className="font-medium">{c.id}</span> {c.label}
                    {c.note && <p className="mt-0.5 max-w-lg text-[11px] text-gray-500">{c.note}</p>}
                  </Td>
                  <Td right>{c.unit === "원" ? won(c.left) : c.left ?? NO_DATA}</Td>
                  <Td right>{c.unit === "원" ? won(c.right) : c.right ?? NO_DATA}</Td>
                  <Td right>
                    <span className={c.diff && Number(c.diff) !== 0 ? "font-medium text-red-700" : ""}>
                      {c.unit === "원" ? won(c.diff) : c.diff ?? NO_DATA}
                    </span>
                  </Td>
                  <Td><Verdict v={c.verdict} /></Td>
                </tr>
              ))}
            </Table>
          )}
      </Card>

      {/* ── 2단: 사다리(참고 표시 — 손익 화면과 같은 값) ── */}
      {ladder && (
        <Card title="손익 사다리 (손익 화면과 같은 함수 산출)"
              right={ladder.basis === "costed_subset"
                ? <Badge tone="alert">원가 확인 {pct(ladder.cost_coverage)}분만</Badge>
                : ladder.basis === "full" ? <Badge tone="neutral">기간 전체</Badge> : undefined}>
          {ladder.blocked ? (
            <div className="px-4 py-3"><EmptyState reason="손익 없음" hint={ladder.blocked.reason} /></div>
          ) : (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1 px-4 py-3 text-sm md:grid-cols-3">
              <KV k="우리 매출(납품가)" v={won(ladder.revenue)} />
              <KV k="− 원가" v={won(ladder.cost)} />
              <KV k="− 분담금" v={won(ladder.promo_burden)} />
              <KV k="− 광고비" v={won(ladder.ad_spend)} />
              <KV k="− 납부세액" v={won(ladder.vat)} />
              <KV k="= 순이익" v={<b>{won(ladder.net_profit)}</b>} />
            </div>
          )}
        </Card>
      )}

      {/* ── 3단: 원자 목록 ── */}
      <Card title={`계산 원자 — 날짜×옵션 (${num(atoms.data?.count)}건, Σ순이익 ${won(atoms.data?.totals.net_profit)})`}
            right={
              <div className="flex items-center gap-2 text-xs">
                <select value={flt} onChange={(e) => setFlt(e.target.value)}
                        className="rounded border-gray-300 py-1 text-xs">
                  <option value="all">전체</option>
                  <option value="loss">적자만</option>
                  <option value="suggested">원가=이름유사도만</option>
                  <option value="uncosted">원가 없음만</option>
                  <option value="unpriced">단가 없음만</option>
                </select>
                <select value={sort} onChange={(e) => setSort(e.target.value)}
                        className="rounded border-gray-300 py-1 text-xs">
                  <option value="revenue">매출순</option>
                  <option value="net">적자순</option>
                  <option value="date">날짜순</option>
                </select>
              </div>
            }>
        {atoms.error ? <EmptyState reason="원자 조회 실패" hint={String(atoms.error)} />
          : !atoms.data ? <Loading />
          : atoms.data.atoms.length === 0 ? <EmptyState reason="조건에 맞는 원자가 없습니다" />
          : (
            <>
              {atoms.data.option_table_truncated && (
                <p className="mx-4 mt-3 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  ⚠️ 옵션 표가 잘렸습니다({num(atoms.data.option_count)}/{num(atoms.data.option_limit)}) —
                  위 검사의 A2·A7은 이 때문에 「판정 안 함」입니다.
                </p>
              )}
              <Table head={<>
                <Th>날짜</Th><Th>옵션</Th><Th right>수량</Th><Th right>우리 매출</Th>
                <Th right>원가</Th><Th right>분담금</Th><Th right>광고비</Th><Th right>순이익</Th>
                <Th>원가 출처</Th>
              </>}>
                {atoms.data.atoms.map((a) => {
                  const key = `${a.date}|${a.option_id}`;
                  const src = SOURCE_LABEL[a.cost_source];
                  return (
                    <Fragment key={key}>
                      <tr className="cursor-pointer hover:bg-gray-50"
                          onClick={() => setOpen(open === key ? null : key)}>
                        <Td>{a.date}</Td>
                        <Td>
                          <div className="max-w-xs truncate" title={a.product_name ?? a.option_id}>
                            {a.product_name ?? a.option_id}
                          </div>
                          <div className="text-[11px] text-gray-400">옵션 {a.option_id}</div>
                        </Td>
                        <Td right>{num(a.qty)}</Td>
                        <Td right>{won(a.our_revenue)}</Td>
                        <Td right>{won(a.cost)}</Td>
                        <Td right>{won(a.promo_burden)}</Td>
                        <Td right>{won(a.ad_spend)}</Td>
                        <Td right>
                          {a.net_profit != null ? (
                            <span className={Number(a.net_profit) >= 0 ? "text-judge-good" : "font-medium text-judge-bad"}>
                              {won(a.net_profit)}
                            </span>
                          ) : a.net_profit_upper != null ? (
                            <span className="font-medium text-judge-bad">≤ {won(a.net_profit_upper)}</span>
                          ) : NO_DATA}
                        </Td>
                        <Td><span className={`rounded px-1.5 py-0.5 text-[11px] ${src.cls}`}>{src.text}</span></Td>
                      </tr>
                      {open === key && (
                        <tr>
                          <td colSpan={9}>
                            <AtomDetail date={a.date} optionId={a.option_id} from={from} to={to} a6Pass={a6Pass} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </Table>
            </>
          )}
      </Card>
    </div>
  );
}
