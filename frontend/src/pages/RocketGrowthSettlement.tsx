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
import {
  fetchRgOptionPnl,
  fetchCommandCenter,
  type RgOptionPnlResponse,
  type OverviewResponse,
} from "../lib/api";
import { rgFeeNote, rgFeeFactsFromOptionPnl, RECONCILE_WARN_PCT } from "../lib/rgSettlementAxis";
import { won, NO_DATA, isoKST, pctFromFraction } from "../lib/format";
// ★계약 §1-A-3 — 종합 조망과 **같은 컴포넌트**다(사본이 아니다). 사본을 뜨면 같은 정산
//   내역을 두 화면이 다른 금액으로 말하게 되고, 그게 D-CPP-47이 고친 병이다.
import { RgSettlementCard } from "../components/RgSettlementCard";

const ACCOUNTS = [
  { key: "COUPANG_WING1", label: "오픽스" },
  { key: "COUPANG_WING2", label: "오하이테크" },
] as const;

const n = (v: unknown): number => Number(v ?? 0) || 0;

/** 원 단위 반올림 — 백엔드 Decimal의 잔차가 화면에 소수점으로 새지 않게(화면 A와 같은 규율).
 *  공용 `format.won()`은 반올림하지 않고, 그건 전 화면 공용이라 고치지 않는다(계약 §3). */
const wonR = (v: unknown): string => won(Math.round(n(v)));

function yesterdayKST(): string {
  return isoKST(new Date(Date.now() - 86_400_000));
}

export default function RocketGrowthSettlement() {
  const [account, setAccount] = useState<string>(ACCOUNTS[0].key);
  const [data, setData] = useState<RgOptionPnlResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 계정별 정산 내역(계약 §1-A-3) — 종합 조망과 같은 응답·같은 컴포넌트.
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [ovErr, setOvErr] = useState<string | null>(null);

  async function load(acc: string) {
    setErr(null);
    const d = yesterdayKST();
    // ★계정을 바꾸면 «직전 계정의» 값을 먼저 버린다(적대 리뷰 1R P2-6). 안 버리면 새 응답이
    //   올 때까지 다른 법인의 정산 카드가 남는데, 카드에 `account_key`가 찍혀 있어 화면이
    //   스스로 모순된 말을 한다(선택은 오하이테크, 카드는 COUPANG_WING1).
    setData(null);
    setOverview(null);
    try {
      setData(await fetchRgOptionPnl(acc, d, d));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setData(null);
    }
    // ★두 조회를 갈라 둔다: 정산 내역이 못 와도 요율·커버리지·장부대조는 계속 뜬다.
    //   그리고 «못 왔다»를 반드시 말한다 — `RgSettlementCard`는 데이터가 없으면 `null`을
    //   그리므로, 실패를 그대로 두면 카드가 **소리 없이 사라져** 「정산 내역이 없다」로 읽힌다.
    //   이 사슬이 네 번 밟은 「모름이 0/없음으로 접히는」 자리와 같은 모양이다.
    setOvErr(null);
    try {
      setOverview(await fetchCommandCenter(d, d, acc));
    } catch (e) {
      setOvErr(e instanceof Error ? e.message : String(e));
      setOverview(null);
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

      {/* ★적대 리뷰 2R NEW P1 — 이 자리가 없으면 «아직 안 온 것»이 «아니다»로 단정된다.
          ①②③의 칸은 전부 `data?.x`를 읽고 값이 없으면 「요율 미상」·「원가 커버리지 미달 —
          순이익을 내지 않는다」 같은 **결론 문장**으로 떨어진다. 계정 전환 때 직전 계정의 값을
          먼저 버리므로(1R P2-6), 그 가드가 없으면 응답이 오기 전 구간 내내 새 계정에 대해
          그 다섯 문장이 거짓으로 뜬다 — 그 순간 ④만 「불러오는 중…」이라 정직하게 말해
          한 화면이 스스로 비대칭이 된다.
          ⇒ ①②③도 ④와 같은 모양으로 «아직 모른다»를 먼저 말한다. 이 사슬이 밟은
          「모름이 0/아니다로 접히는」 자리의 여섯 번째다. */}
      {data == null ? (
        <div className="bg-white border rounded-md p-4 text-sm text-gray-500">
          {/* ★3R P2 — 초판은 `!err &&`로 «실패»를 이 가드 밖에 뒀다. 그래서 실패 얼굴은
              여전히 ①②③을 그렸고 다섯 결론 문장을 그대로 말했다(origin/main부터의 선재 결함
              이라 회귀는 아니었다). 실패와 로딩은 **다른 문장**이어야 하지만, 둘 다 «모름»이지
              «미상/미달»이 아니다 — 갈라야 할 것은 얼굴이지 결론이 아니다. */}
          {err ? (
            <>
              요율·커버리지·장부대조를 <strong>못 불러왔다</strong> — 위 사유 참조.{" "}
              <strong>«미상/미달»이 아니라 «모름»이다.</strong>
            </>
          ) : (
            <>
              요율·커버리지·장부대조를 불러오는 중… —{" "}
              <strong>«모른다»이지 «미상/미달»이 아니다.</strong>
            </>
          )}
        </div>
      ) : (
        <>
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
                · 매출 {wonR((data.account_common.fee_unmapped_revenue))}에는 비용을 못 붙였다
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
                · 원가 미상 매출 {wonR((data.account_common.cost_unmapped_revenue))}
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
              <dd>{wonR((rec.computed))}</dd>
              <dt className="text-gray-500">원장 실청구액</dt>
              <dd>{wonR((rec.actual))}</dd>
              <dt className="text-gray-500">차이</dt>
              <dd className="font-medium">
                {n(rec.diff) >= 0 ? "+" : "−"}
                {wonR(Math.abs(n(rec.diff)))}
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
        </>
      )}

      {/* ④ 주기별 정산 내역 (계약 §1-A-3) — 종합 조망과 «같은 컴포넌트»를 재사용한다.
          위 ①②③은 «우리가 계산한 것»의 근거이고, 이 카드는 «쿠팡이 실제로 청구한 원장»이다.
          둘을 한 화면에 나란히 두는 것이 이 화면의 존재 이유다. */}
      <div>
        <div className="font-medium mb-2">주기별 정산 내역 (쿠팡 원장)</div>
        <p className="text-xs text-gray-500 mb-2">
          「어제」({yesterdayKST()})를 덮는 정산 주기의 계정별 청구 내역이다. 계정 카드의 금액은{" "}
          <strong>정산주기 기준이라 부분 윈도우도 주기 전액</strong>이다 — 위 ①②③(판매일 축)과 값이
          다른 것이 정상이고, 그 차이를 재는 자리가 ③이다. (카드 헤드라인은 축을 탈 수 있어 카드
          자신이 어느 축인지 말한다.) 종합 조망의 그 카드와 <strong>같은 컴포넌트·같은 응답</strong>
          이다 — 사본이 아니라서 <strong>금액이 갈라질 수 없다</strong>.
          {/* ★2R NEW P2① — 「갈라질 수 없다」를 무조건형으로 쓰면 안 된다. 원장 row 0건일 때
              이 화면은 아래에서 «아직 안 들어왔다»로 막는데 종합 조망은 같은 응답으로 여전히
              「✅ 순이익 반영됨 / 정산총액 0원」을 그린다(origin/main의 기존 동작 — 이 PR이
              만든 것이 아니다). 그러니 여기서 보증하는 것은 «금액»이지 «결손 표현»이 아니다.
              결손 표현까지 맞추려면 가드를 공용 카드 안으로 옮겨야 하는데, 그건 종합 조망의
              동작을 바꾸는 일이라 이 계약(§1-A-3)의 범위 밖이다 → 앵커 `## 이월`. */}
        </p>
        {ovErr ? (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded p-3 text-sm">
            정산 내역을 못 불러왔다 — {ovErr}. <strong>못 잰 것이지 「내역이 없다」가 아니다.</strong>
          </div>
        ) : overview == null ? (
          <div className="bg-white border rounded-md p-3 text-sm text-gray-500">불러오는 중…</div>
        ) : overview.rg_settlement == null ? (
          /* 방어용 — 현재 백엔드는 이 키를 항상 싣는다(`intelligence.py`의 단일 return).
             그래서 «이 분기에 가드를 걸어 둔 것»만으로는 아무것도 못 막는다: 실제로 도달하는
             결손 상태는 바로 아래 `by_account.length === 0`이다(적대 리뷰 1R P1). */
          <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-900">
            이 창에 RG 정산 원장이 응답에 없다 — <strong>0원이 아니라 «없음»</strong>이다.
            갱신은 종합 조망의 「RG 정산 갱신」에서 한다(같은 데몬을 두 화면이 동시에 부르지 않게).
          </div>
        ) : (overview.rg_settlement.by_account?.length ?? 0) === 0 ? (
          /* `?.` — 바로 위 `rg_settlement == null`은 «죽은 분기»인 걸 알면서도 방어로 남겼는데
             형제 필드는 크래시하는 비대칭이었다(2R NEW P2). 방어를 할 거면 같은 깊이로 한다. */
          /* ★적대 리뷰 1R P1 — 이 사슬이 밟은 「모름이 0으로 접히는」 자리의 **다섯 번째**.
             카드는 `has_data`만 보고 ✅ 녹색으로 「정산총액 0원」을 단정하는데, `has_data`는
             «판매일 축 차감액이 0이 아님»으로도 참이 된다(원장 row와 독립). 그런데 RG 정산
             성숙도는 D+12이고 이 화면은 창을 «어제»(D-1)로 고정한다 — 즉 **어제 판매가 있고
             원장이 아직 안 들어온 날 = 거의 매일**이 이 상태다. 그 카드는 「아래 계정 카드」를
             가리키는 문장까지 그리는데 계정 카드가 0개다.
             ⇒ 카드를 그리지 않고, 0원이 아니라 «아직»임을 말한다. */
          <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-900">
            이 창의 정산 원장엔 계정 row가 <strong>0건</strong>이다 —{" "}
            <strong>0원이 아니라 «아직 안 들어왔다»</strong>. RG 정산은 성숙까지 <strong>D+12</strong>
            가 걸리는데 이 화면은 「어제」를 본다. 위 ①②③(판매일 축)은 그대로 유효하다 — 그건 원장이
            아니라 그 날 판 것에서 계산한 값이다.
          </div>
        ) : (
          /* 갱신 props를 안 넘긴다 = 읽기 전용 렌더. 이유는 컴포넌트 파일 머리말 참조. */
          <RgSettlementCard data={overview} />
        )}
      </div>
    </div>
  );
}
