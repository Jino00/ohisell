// RocketGrowthSettlement.tsx — RG(로켓그로스 2P) «자기 화면» ②: 이 숫자를 믿어도 되는가.
// 계약 `docs/contracts/CONTRACT_2p_own_screens.md`(D-CPP-54) §1-A-3 · 합격 ⓕ.
//
// ★왜 이 화면이 따로 있나 — 직전 계약의 완료 QA가 남긴 판정문이 이 화면의 존재 이유다:
//     *"핵심 숫자는 화면에 정확히 뜨나, 그 숫자를 믿어도 되는지 사용자가 확인할 길
//       (요율 출처·커버리지·보존식 자백)이 아직 화면 밖에 갇혀 있다"*
//   대시보드 행의 한 줄짜리 자백은 «있다/없다»만 말한다. 여기서는 그 근거를 펼친다.
//
// ★새 계산은 0이다(계약 §3). 전부 백엔드가 이미 낸 값의 표시다.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchRgOptionPnl, type RgOptionPnlResponse } from "../lib/api";
import { rgFeeNote, rgFeeFactsFromOptionPnl, RECONCILE_WARN_PCT } from "../lib/rgSettlementAxis";
import { won, NO_DATA, isoKST, pctFromFraction } from "../lib/format";

const ACCOUNTS = [
  { key: "COUPANG_WING1", label: "오픽스" },
  { key: "COUPANG_WING2", label: "오하이테크" },
] as const;

const n = (v: unknown): number => Number(v ?? 0) || 0;

function yesterdayKST(): string {
  return isoKST(new Date(Date.now() - 86_400_000));
}

export default function RocketGrowthSettlement() {
  const [account, setAccount] = useState<string>(ACCOUNTS[0].key);
  const [data, setData] = useState<RgOptionPnlResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function load(acc: string) {
    setErr(null);
    try {
      const d = yesterdayKST();
      setData(await fetchRgOptionPnl(acc, d, d));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setData(null);
    }
  }

  useEffect(() => {
    void load(account);
  }, [account]);

  const note = data
    ? rgFeeNote(rgFeeFactsFromOptionPnl(data as unknown as Record<string, unknown>))
    : null;
  const rec = data?.reconciliation ?? null;
  const recPct = rec?.diff_pct == null ? null : n(rec.diff_pct);
  const recWarn = recPct != null && Math.abs(recPct) > RECONCILE_WARN_PCT;

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-bold">🧾 로켓그로스 정산·근거</h1>
        <p className="text-sm text-gray-500 mt-1">
          손익 화면의 정산공제가 «무엇을 근거로» 나왔는지. 요율은 어디서 쟀나, 얼마나 덮었나,
          장부 총액과 얼마나 어긋나나 — <strong>차이를 0으로 맞추지 않고 그대로 보인다.</strong>
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 bg-white border rounded-md p-3">
        <span className="text-sm text-gray-600">계정</span>
        {ACCOUNTS.map((a) => (
          <button
            key={a.key}
            onClick={() => setAccount(a.key)}
            className={`px-3 py-1 rounded text-sm ${
              account === a.key ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-700"
            }`}
          >
            {a.label}
          </button>
        ))}
        <Link to="/rocket-growth" className="ml-auto text-sm text-blue-600 underline">
          ← 상품별 손익으로
        </Link>
      </div>

      {err && <div className="bg-red-50 border border-red-200 text-red-700 rounded p-3 text-sm">{err}</div>}

      {note && (
        <div
          className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-900"
          title={note.title}
        >
          {note.text}
        </div>
      )}

      {/* ① 판매수수료 요율의 출처 (계약 §4 ⓕ) */}
      <div className="bg-white border rounded-md p-4">
        <div className="font-medium mb-2">판매수수료 요율의 출처</div>
        <dl className="text-sm grid grid-cols-[10rem_1fr] gap-y-1">
          <dt className="text-gray-500">적용 요율</dt>
          <dd>{data?.rate == null ? NO_DATA : `${(n(data.rate) * 100).toFixed(4)}%`}</dd>
          <dt className="text-gray-500">근거</dt>
          <dd>
            {data?.rate_basis === "settled_rate" ? (
              <span className="text-green-700">실측 (완결 정산주기에서 역산)</span>
            ) : (
              <span className="text-amber-700">
                요율 미상 — 잴 완결 주기가 없다. <strong>기본 요율로 추정하지 않는다</strong>(계약 §8-4).
              </span>
            )}
          </dd>
          <dt className="text-gray-500">근거 주기</dt>
          <dd>{data?.rate_cycles || NO_DATA}</dd>
          <dt className="text-gray-500">공제 축</dt>
          <dd>
            {data?.commission_axis === "sales_date" ? (
              "판매일 축 — 그 날 판 것에 붙는 비용"
            ) : (
              <span className="text-amber-700">
                정산 인식일 축 — 판매일 축을 못 냈다(주기 통짜라 어느 하루를 물어도 같다)
              </span>
            )}
          </dd>
        </dl>
      </div>

      {/* ② 얼마나 덮었나 */}
      <div className="bg-white border rounded-md p-4">
        <div className="font-medium mb-2">얼마나 덮었나 (커버리지)</div>
        <dl className="text-sm grid grid-cols-[10rem_1fr] gap-y-1">
          <dt className="text-gray-500">물류비 단가</dt>
          <dd>
            {data?.fee_coverage == null ? NO_DATA : pctFromFraction(n(data.fee_coverage))}
            {data?.account_common && n(data.account_common.fee_unmapped_revenue) > 0 && (
              <span className="text-amber-700">
                {" "}
                · 매출 {won(n(data.account_common.fee_unmapped_revenue))}에는 비용을 못 붙였다
                (0으로 «채우지 않았다» — 그만큼 공제는 하한이다)
              </span>
            )}
          </dd>
          <dt className="text-gray-500">원가</dt>
          <dd>
            {data?.cost_coverage == null ? NO_DATA : pctFromFraction(n(data.cost_coverage))}
            {data?.account_common && n(data.account_common.cost_unmapped_revenue) > 0 && (
              <span className="text-amber-700">
                {" "}
                · 원가 미상 매출 {won(n(data.account_common.cost_unmapped_revenue))}
              </span>
            )}
          </dd>
          <dt className="text-gray-500">옵션축 적재일</dt>
          <dd>
            {data?.option_axis_days || NO_DATA}
            {data && !data.option_axis_complete && (
              <span className="text-amber-700"> · 창을 다 못 덮었다</span>
            )}
          </dd>
          <dt className="text-gray-500">순이익을 낼 수 있나</dt>
          <dd>
            {data?.cost_trustworthy ? (
              "원가 게이트 통과"
            ) : (
              <span className="text-amber-700">
                원가 커버리지 미달 — <strong>순이익을 내지 않는다</strong>(광고비 하한만).
                3P·네이버엔 없는 게이트다.
              </span>
            )}
          </dd>
        </dl>
      </div>

      {/* ③ 장부 총액 대조 (계약 §4 ⓕ) */}
      <div
        className={`border rounded-md p-4 ${
          recWarn ? "bg-red-50 border-red-200" : "bg-white"
        }`}
      >
        <div className="font-medium mb-2">
          장부 총액 대조 {recWarn && <span className="text-red-700">⚠️ 임계 초과</span>}
        </div>
        {rec == null ? (
          <div className="text-sm text-gray-500">
            완결 정산주기가 없어 대조할 수 없다 — <strong>못 잰 것이지 0이 아니다</strong>.
          </div>
        ) : (
          <>
            <dl className="text-sm grid grid-cols-[10rem_1fr] gap-y-1">
              <dt className="text-gray-500">완결 주기</dt>
              <dd>
                {rec.cycle_from} ~ {rec.cycle_to}
              </dd>
              <dt className="text-gray-500">이 방식의 합</dt>
              <dd>{won(n(rec.computed))}</dd>
              <dt className="text-gray-500">원장 실청구액</dt>
              <dd>{won(n(rec.actual))}</dd>
              <dt className="text-gray-500">차이</dt>
              <dd className="font-medium">
                {n(rec.diff) >= 0 ? "+" : "−"}
                {won(Math.abs(n(rec.diff)))}
                {recPct != null && ` (${recPct >= 0 ? "+" : ""}${recPct.toFixed(2)}%)`}
              </dd>
            </dl>
            <p className="text-xs text-gray-500 mt-2">
              임의의 창은 정산 주기 경계와 안 맞아 원장 총액과 비교하는 것 자체가 뜻이 없다 —
              완결 주기가 분자·분모가 같은 기간을 가리키는 유일한 자리다.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
