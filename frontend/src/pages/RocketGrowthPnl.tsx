// RocketGrowthPnl.tsx — RG(로켓그로스 2P) «자기 화면» ①: 그 날 판 상품별 손익.
// 계약 `docs/contracts/CONTRACT_2p_own_screens.md`(D-CPP-54) §1-A-2 · 합격 ⓑⓒⓓⓔ.
//
// Jino 원문(이 화면은 이 한 문장의 직역이다):
//   *"어제 어떤 제품이 몇개가 팔리고 그 판매분의 정산공제, 원가, 세금, 기타비용등을 빼고
//     남는 이익이 있잖아. 다른 판매와 같이 2P도 그걸 보자는거지"*
//
// ★새 계산이 없다(계약 §3 금지선). 백엔드 `rg_daily_pnl.rg_option_pnl()`이 대시보드 RG 행과
//   «같은 다섯 항»을 날짜×옵션 grain으로 분해해 준 것을 그대로 그린다.
// ★표가 두 덩어리인 이유(§1-A-2): 상품에 붙는 것(물류비·판매수수료·원가·광고비)과 상품에
//   **못 붙는 것**(보관비 일할·납부세액·미배분 광고비·축 차이)은 섞으면 안 된다. 섞으면 상품
//   행 합계가 «순이익 전부»인 척하게 되고, 그게 이 트랙이 두 번 고친 병이다.
// ★자백 문장은 여기서 새로 쓰지 않는다 — `rgFeeNote()`가 대시보드·종합조망과 같은 문장을 낸다.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchRgOptionPnl, type RgOptionPnlResponse } from "../lib/api";
import { rgFeeNote, rgFeeFactsFromOptionPnl } from "../lib/rgSettlementAxis";
import { won, num, NO_DATA, isoKST } from "../lib/format";

/** 계정 = 법인이다. 발단 문장이 「오픽스」였으므로 화면이 계정을 말해야 한다. */
const ACCOUNTS = [
  { key: "COUPANG_WING1", label: "오픽스" },
  { key: "COUPANG_WING2", label: "오하이테크" },
] as const;

function yesterdayKST(): string {
  return isoKST(new Date(Date.now() - 86_400_000));
}

const n = (v: unknown): number => Number(v ?? 0) || 0;

/** 값이 «없다»와 «0이다»를 가른다 — null은 0으로 그리지 않는다(계약 §3 추정 금지). */
function cell(v: string | null): string {
  return v == null ? NO_DATA : won(n(v));
}

export default function RocketGrowthPnl() {
  const [account, setAccount] = useState<string>(ACCOUNTS[0].key);
  const [from, setFrom] = useState<string>(yesterdayKST());
  const [to, setTo] = useState<string>(yesterdayKST());
  const [data, setData] = useState<RgOptionPnlResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    setErr(null);
    try {
      setData(await fetchRgOptionPnl(account, from, to));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const note = data ? rgFeeNote(rgFeeFactsFromOptionPnl(data as unknown as Record<string, unknown>)) : null;
  const rows = data?.options ?? [];
  const ac = data?.account_common;
  const cons = data?.conservation;

  // 상품 행 소계 — «상품에 붙는 것만»의 합이다. 계정 공통 행과 더해야 총 순이익이 된다.
  const optSum = rows.reduce((s, r) => s + n(r.net_profit), 0);

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-bold">🌱 로켓그로스 손익 (판매일 축)</h1>
        <p className="text-sm text-gray-500 mt-1">
          그 날 판 상품별로 «몇 개 팔리고, 정산공제·원가·광고비를 빼고 얼마 남았나». 상품에 못 붙는
          비용(보관비·납부세액)은 아래 «계정 공통» 표에 따로 싣는다 — 상품 행 합계만으로 순이익
          전부인 척하지 않는다.
        </p>
      </div>

      {/* 조회 조건 */}
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
        <input
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          className="border rounded px-2 py-1 text-sm"
          aria-label="시작일"
        />
        <span className="text-gray-400">~</span>
        <input
          type="date"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className="border rounded px-2 py-1 text-sm"
          aria-label="종료일"
        />
        <button
          onClick={() => {
            setFrom(yesterdayKST());
            setTo(yesterdayKST());
          }}
          className="px-3 py-1 rounded text-sm bg-gray-100 text-gray-700"
        >
          어제
        </button>
        <button
          onClick={() => void load()}
          className="px-4 py-1 rounded text-sm bg-blue-600 text-white"
          disabled={loading}
        >
          {loading ? "조회 중…" : "조회"}
        </button>
        <Link to="/rocket-growth/settlement" className="ml-auto text-sm text-blue-600 underline">
          정산·근거 보기 →
        </Link>
      </div>

      {err && <div className="bg-red-50 border border-red-200 text-red-700 rounded p-3 text-sm">{err}</div>}

      {/* ★자백 — 축·요율 근거·커버리지·장부대조. 대시보드·종합조망과 «같은 문장»이다. */}
      {note && (
        <div
          className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-900"
          title={note.title}
        >
          {note.text}
        </div>
      )}
      {data?.ad_spend_warning && (
        <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-900">
          ⚠️ 광고비 미상 — {data.ad_spend_warning}
        </div>
      )}

      {/* 상품 행 */}
      <div className="bg-white border rounded-md overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left px-3 py-2">상품 / 옵션ID</th>
              <th className="text-right px-3 py-2">판매수량</th>
              <th className="text-right px-3 py-2">매출</th>
              <th className="text-right px-3 py-2">물류비</th>
              <th className="text-right px-3 py-2">판매수수료</th>
              <th className="text-right px-3 py-2">원가</th>
              <th className="text-right px-3 py-2">광고비</th>
              <th className="text-right px-3 py-2">남는 이익</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !loading && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-gray-400">
                  그 창에 판매도 광고도 없다
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.vendor_item_id} className="border-t">
                <td className="px-3 py-2">
                  <div>{r.name || NO_DATA}</div>
                  <div className="text-xs text-gray-400">{r.vendor_item_id}</div>
                </td>
                <td className="text-right px-3 py-2">{num(r.units_sold)}</td>
                <td className="text-right px-3 py-2">{won(n(r.revenue))}</td>
                <td className="text-right px-3 py-2">{cell(r.fee_logistics)}</td>
                <td className="text-right px-3 py-2">{cell(r.fee_sale_fee)}</td>
                <td className="text-right px-3 py-2">
                  {r.has_cost ? won(n(r.cost)) : <span className="text-amber-600">원가 미상</span>}
                </td>
                <td className="text-right px-3 py-2">{won(n(r.ad_spend))}</td>
                <td className="text-right px-3 py-2 font-medium">{cell(r.net_profit)}</td>
              </tr>
            ))}
            {rows.length > 0 && (
              <tr className="border-t bg-gray-50 font-medium">
                <td className="px-3 py-2">상품 행 소계</td>
                <td colSpan={6} />
                <td className="text-right px-3 py-2">{won(optSum)}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ★계정 공통 — 상품에 «못 붙는» 것. 0으로 채우지 않고 여기서 자백한다. */}
      {ac && (
        <div className="bg-white border rounded-md p-3">
          <div className="font-medium mb-2">계정 공통 (상품에 못 붙는 것)</div>
          <table className="w-full text-sm">
            <tbody>
              <tr className="border-t">
                <td className="px-3 py-2">보관비·반품비 (기간비용 일할)</td>
                <td className="text-right px-3 py-2">−{won(n(ac.period_fees))}</td>
                <td className="px-3 py-2 text-xs text-gray-500">
                  판매일에 안 붙인다 — 매출 0인 주기에도 발생하는 재고 보유 비용이다(계약 §8-5).
                </td>
              </tr>
              <tr className="border-t">
                <td className="px-3 py-2">납부세액</td>
                <td className="text-right px-3 py-2">−{won(n(ac.payable_vat))}</td>
                <td className="px-3 py-2 text-xs text-gray-500">계정 단위라 상품 행에 안 붙인다.</td>
              </tr>
              {n(ac.revenue_axis_gap) !== 0 && (
                <tr className="border-t">
                  <td className="px-3 py-2">매출 축 차이 (요약축 − 옵션축)</td>
                  <td className="text-right px-3 py-2">{won(n(ac.revenue_axis_gap))}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">
                    대시보드 매출은 요약축, 상품 행은 옵션축이다. 차액을 상품에 우겨넣지 않는다.
                  </td>
                </tr>
              )}
              {n(ac.fee_axis_fallback_gap) !== 0 && (
                <tr className="border-t">
                  <td className="px-3 py-2">원장 축 폴백 잔여</td>
                  <td className="text-right px-3 py-2">−{won(n(ac.fee_axis_fallback_gap))}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">
                    이 창은 판매일 축을 못 내 원장 축으로 물러섰다 — 옵션별로 못 가르는 몫이다.
                  </td>
                </tr>
              )}
              {n(ac.ad_unallocated) !== 0 && (
                <tr className="border-t">
                  <td className="px-3 py-2">미배분 광고비 ({ac.ad_unallocated_options}옵션)</td>
                  <td className="text-right px-3 py-2">{won(n(ac.ad_unallocated))}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">
                    그 옵션ID가 우리 원장 어디에도 없다 — <strong>이 손익엔 안 실린다</strong>.
                    추정으로 배분하지 않는다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ★보존식 — 이 화면이 대시보드와 같은 말을 하는가. 차이를 0으로 숨기지 않는다. */}
      {cons && (
        <div
          className={`border rounded-md p-3 text-sm ${
            cons.ok ? "bg-green-50 border-green-200 text-green-900" : "bg-red-50 border-red-200 text-red-800"
          }`}
        >
          <div className="font-medium">
            {cons.ok ? "✅ 대시보드 RG 행과 일치" : "⚠️ 대시보드 RG 행과 어긋남"}
          </div>
          <div className="mt-1">
            상품 행 소계 {won(n(cons.options_net_sum))} + 계정 공통 {won(n(cons.account_common_sum))} ={" "}
            <strong>{cons.computed_total_net == null ? NO_DATA : won(n(cons.computed_total_net))}</strong>
            {" / 대시보드 "}
            {cons.reference_net == null ? NO_DATA : won(n(cons.reference_net))}
            {" / 차이 "}
            <strong>{cons.diff == null ? NO_DATA : won(n(cons.diff))}</strong>
          </div>
        </div>
      )}
    </div>
  );
}
