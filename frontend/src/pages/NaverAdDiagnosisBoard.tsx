// NaverAdDiagnosisBoard.tsx — 네이버 SA 광고 진단 보드 (P2-S2, track_naver-ad-optimization)
// GET /api/naver/ad/diagnosis 시각화: 출혈/굶는승자/확장버킷/쇼핑그룹BEP/제외후보/3단분류/악순환
// 전부 읽기 전용(D-3) — 제안·쓰기 없음, 사실/지표만 정리.
import { useEffect, useRef, useState } from "react";
import {
  fetchNaverAdDiagnosis,
  type NaverAdDiagnosis,
  type NaverAdDiagnosisKeywordRow,
  type NaverAdDiagnosisFloorWaitRow,
} from "../lib/api";
import { isoKST, num, won, pctFromFraction, roasX, NO_DATA } from "../lib/format";
import { LayerNav } from "../components/ui";

function daysAgo(n: number): string {
  return isoKST(new Date(Date.now() - n * 86400000));
}

function keywordLabel(r: NaverAdDiagnosisKeywordRow): string {
  return r.keyword_id ? `${r.adgroup_id} / ${r.keyword_id}` : r.adgroup_id;
}

// UI3(D-NAO-65): 바닥 대기 유닛 라벨 — 키워드는 그룹/키워드, 쇼핑은 캠페인/그룹.
function floorWaitLabel(r: NaverAdDiagnosisFloorWaitRow): string {
  return r.target_type === "keyword"
    ? `${r.adgroup_id} / ${r.keyword_id ?? ""}`
    : `${r.campaign_id} / ${r.adgroup_id}`;
}

function Board({
  title,
  subtitle,
  count,
  children,
}: {
  title: string;
  subtitle: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-gray-700">{title}</h3>
          <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>
        </div>
        {count !== undefined && <span className="text-xs text-gray-400">{count}건</span>}
      </div>
      {children}
    </div>
  );
}

function EmptyRow({ text = "해당 없음" }: { text?: string }) {
  return <div className="p-6 text-center text-gray-400 text-sm">{text}</div>;
}

function KeywordTable({ rows, showAvgClk }: { rows: NaverAdDiagnosisKeywordRow[]; showAvgClk?: boolean }) {
  if (rows.length === 0) return <EmptyRow />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="bg-gray-50 border-b border-gray-200">
            <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">그룹/키워드</th>
            <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">노출</th>
            <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">클릭</th>
            {showAvgClk && <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">일평균클릭</th>}
            <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">광고비</th>
            <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">전환매출</th>
            <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">ROAS(보정)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.campaign_id}/${r.adgroup_id}/${r.keyword_id}#${i}`} className="hover:bg-gray-50">
              <td className="px-4 py-2 text-sm border-b border-gray-100">{keywordLabel(r)}</td>
              <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.imp)}</td>
              <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.clk)}</td>
              {showAvgClk && (
                <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">
                  {r.avg_daily_clk?.toFixed(2) ?? NO_DATA}
                </td>
              )}
              <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.cost)}</td>
              <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.conv_amt)}</td>
              <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums font-medium">
                {roasX(r.roas_corrected)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function NaverAdDiagnosisBoard() {
  const today = isoKST(new Date());
  const [dateFrom, setDateFrom] = useState(daysAgo(14));
  const [dateTo, setDateTo] = useState(today);
  const [data, setData] = useState<NaverAdDiagnosis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reqSeq = useRef(0);

  async function load() {
    const mySeq = ++reqSeq.current;
    setLoading(true);
    setError(null);
    try {
      const d = await fetchNaverAdDiagnosis({ dateFrom, dateTo });
      if (mySeq !== reqSeq.current) return;
      setData(d);
    } catch (e: any) {
      if (mySeq !== reqSeq.current) return;
      setError(e.message);
    } finally {
      if (mySeq === reqSeq.current) setLoading(false);
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [dateFrom, dateTo]);

  const boards = data?.boards;
  const triage = boards?.keyword_triage;
  const expansion = boards?.expansion_bucket;

  return (
    <div className="space-y-6">
      <LayerNav />
      {/* 필터바 */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-gray-600">진단 창(출혈·승자·확장버킷·쇼핑BEP)</span>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1" />
          <span className="text-gray-400">~</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1" />
          <span className="text-xs text-gray-400 ml-2">기본 15일 — 실측 베이스라인과 동일 창</span>
        </div>
      </div>

      {error && <div className="bg-red-50 border border-red-200 text-red-600 text-sm rounded-lg p-3">{error}</div>}

      {loading && !data ? (
        <div className="h-40 bg-gray-50 rounded-lg animate-pulse" />
      ) : data?.error ? (
        <div className="bg-amber-50 border border-amber-200 text-amber-700 text-sm rounded-lg p-4">
          {data.error}
        </div>
      ) : data && boards && triage && expansion ? (
        <>
          {/* 계정 요약 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-white rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500 mb-1">계정 BEP ROAS</div>
              <div className="text-lg font-semibold text-gray-900 tabular-nums">{roasX(data.account_bep_roas)}</div>
            </div>
            <div className="bg-white rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500 mb-1">계정 목표 ROAS</div>
              <div className="text-lg font-semibold text-gray-900 tabular-nums">{roasX(data.account_target_roas)}</div>
            </div>
            <div className="bg-white rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500 mb-1">D-NAO-21 보정계수 (구간 자)</div>
              {/* ★D-NAO-230 계약 §5-5: 점추정 하나가 아니라 **구간 양끝을 병기**한다.
                  분자에 광고 귀속 조인이 없어 「채널 매출 100%를 광고가 견인」 가정과 동치이고,
                  계정 총이익의 부호가 이 값 하나에 달려 있다 — 한 숫자로 쓰면 그 사실이 숨는다. */}
              <div className="text-lg font-semibold text-gray-900 tabular-nums">
                ×{data.correction_factor.factor_low.toFixed(4)} ~ ×
                {data.correction_factor.factor_high.toFixed(4)}
              </div>
              <div className="text-xs text-gray-400 mt-0.5" data-testid="factor-window">
                {data.correction_factor.source === "actual_revenue_ratio"
                  ? `${data.correction_factor.window_from}~${data.correction_factor.window_to} · 점추정 ×${data.correction_factor.factor_point.toFixed(4)}`
                  : "산출 불가(1.0 폴백)"}
              </div>
              {/* ★★D-NAO-234 — 하한의 «근거»를 값 옆에 같이 쓴다.
                  옛 문구는 「하한=보정 없음」이었다. 그건 1.0을 하한이라 부른 것이지 근거가 아니었고,
                  inflowPath 실측(ref 95)이 그보다 낮은 정합 측정 0.827을 주면서 반증됐다.
                  계약 §4 금지선 5: 가정(마지막터치·창·플러스스토어 처분) 병기 없이 내보내지 않는다. */}
              {data.correction_factor.factor_low_source ? (
                <div className="text-[11px] text-gray-400 mt-1 leading-snug" data-testid="factor-low-basis">
                  {/* ★적대 리뷰 P1-3: 실측 기준선 0.827은 «항상 하한»이 아니다 — 점추정이 그보다
                      낮게 재확정되면 기준선이 «상한» 자리로 올라간다. 화면이 그 위치를 말해야
                      「하한 근거」라는 이름표와 실제 값이 어긋나지 않는다. */}
                  <span className="text-gray-500">
                    {data.correction_factor.factor_floor_end === "high" ? "상한" : "하한"} 근거
                  </span>
                  {data.correction_factor.factor_floor !== undefined ? (
                    <span data-testid="factor-floor-value">
                      {` (실측 기준선 ×${data.correction_factor.factor_floor.toFixed(4)})`}
                    </span>
                  ) : null}
                  : 유입경로 「광고&gt;」 5종 매출 ÷ 광고 direct 전환매출
                  {data.correction_factor.factor_low_window ? ` (창 ${data.correction_factor.factor_low_window})` : ""} —{" "}
                  <span className="text-gray-500">마지막터치 라벨 기준</span>.{" "}
                  {data.correction_factor.factor_floor_end === "high" && (
                    <span className="text-gray-500">
                      ⚠️점추정이 실측 기준선보다 낮아 기준선이 «상한» 자리에 있다 — 구간이 통째로
                      기준선 아래로 내려갔다는 뜻이다.{" "}
                    </span>
                  )}
                  {data.correction_factor.factor_low_caveat}
                  {data.correction_factor.factor_low_window_spread
                    ? ` 창을 바꾸면 ${data.correction_factor.factor_low_window_spread}.`
                    : ""}
                </div>
              ) : (
                <div className="text-[11px] text-gray-400 mt-1 leading-snug" data-testid="factor-low-basis">
                  <span className="text-gray-500">실측 기준선 미적용</span> — 계수를 못 만들어 구간이 [1, 1]로 퇴화했다(보정 안 함).
                </div>
              )}
              <div className="text-[11px] text-gray-400 mt-1 leading-snug" data-testid="factor-end-assignment">
                상한=채널매출÷광고전환매출(광고 귀속 조인 없음 ={" "}
                <span className="text-gray-500">100% 견인 가정</span>).{" "}
                {/* ★D-NAO-234 ⓐ: 층이 셋이라는 것까지는 D-NAO-232가 밝혔고, 이 PR이 **게이트를 상한으로
                    재배정**했다. 화면 문구를 같이 안 고치면 배포 동작과 정반대인 카드가 남는다
                    (n=39 P1-1·n=40 P1-2가 연속으로 그 모양이었다). */}
                층은 셋이다 — <span className="text-gray-500">«선정»은 상한</span>(액셀 판정 불변),{" "}
                <span className="text-gray-500">«게이트»(통과·차단)도 상한</span>(D-NAO-234 ⓐ — 하한을 쓰면
                하한이 내려갈수록 차단이 늘어 브레이크가 커진다), 하한은{" "}
                <span className="text-gray-500">«크기»에만</span> 쓴다(입찰 크기·서보 경제성 상한·확장 배분).
              </div>
            </div>
            <div className="bg-white rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500 mb-1">진단 창</div>
              <div className="text-sm font-medium text-gray-900">{data.window.date_from} ~ {data.window.date_to}</div>
            </div>
          </div>

          {/* ★D-NAO-232 계약 §4-④ — 「액셀이 실행 게이트에서 얼마나 죽는가」.
              북극성 §7이 *"자동화 범위를 넓힐 때마다 「액셀·브레이크가 대칭인가」를 검사 항목으로
              둘 것"* 이라 정했는데 그 검사가 화면에 없어서 세션마다 사람이 curl로 다시 셌다.
              여기 있으면 다시 안 센다. 관측 전용 — 어떤 판정·필터도 바꾸지 않는다(ref 94 §5·§6). */}
          {data.accel_gate ? (
            <div className="bg-white rounded-lg border border-gray-200 p-4" data-testid="accel-gate-card">
              <div className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
                <h2 className="text-sm font-semibold text-gray-900">⚖️ 액셀 게이트 — BEP 증액금지가 막는 것</h2>
                {/* ★testid로 «위치»를 고정한다 — 적대 리뷰 1R 변이 M1(헤드라인의 survive_low를
                    survive_high로 바꿔 「막힌 게 없다」로 말하기)이 생존한 이유가, 테스트가
                    card.textContent 어딘가에 "195"만 있으면 만족했기 때문이다. */}
                {/* ★★D-NAO-234 ⓐ: 통과 건수를 `survive_low`로 **못 박아 두면** 게이트가 상한으로
                    재배정된 뒤에도 화면이 옛 끝을 말한다. 어느 끝을 쓰는지는 백엔드의 `gate_end`가
                    유일한 진실이므로 **그것을 읽어 고른다** — 화면이 자기 사본으로 판정하지 않게. */}
                <div className="text-xs text-gray-500 tabular-nums" data-testid="accel-gate-headline">
                  액셀 후보 {num(data.accel_gate.accel_total)}건 → 게이트 통과{" "}
                  <span className="font-semibold text-gray-900" data-testid="accel-gate-survive">
                    {num(
                      data.accel_gate.gate_end === "factor_high"
                        ? data.accel_gate.survive_high
                        : data.accel_gate.survive_low,
                    )}건
                  </span>
                  {data.accel_gate.survive_high !== data.accel_gate.survive_low && (
                    <>
                      {" "}
                      ({data.accel_gate.gate_end === "factor_high" ? "하한" : "상한"}이었다면{" "}
                      {num(
                        data.accel_gate.gate_end === "factor_high"
                          ? data.accel_gate.survive_low
                          : data.accel_gate.survive_high,
                      )}건)
                    </>
                  )}
                </div>
              </div>
              <div className="text-[11px] text-gray-400 mb-3 leading-snug" data-testid="accel-gate-caveats">
                {data.accel_gate.gate_note} 그래서 <span className="text-gray-500">막힌 건의 총이익을 구간 양끝으로 병기</span>한다 —{" "}
                {data.accel_gate.assumption}
                <br />
                {/* ★1R P2-2: 확정값처럼 보이지 않게 창 근사를 화면에서 자백한다 */}
                <span className="text-gray-500">{data.accel_gate.window_caveat}</span>{" "}
                목표ROAS는{" "}
                {data.accel_gate.target_roas_source === "per_campaign" ? (
                  <>
                    <span className="text-gray-500">캠페인별</span>
                    {data.accel_gate.target_roas_min != null && data.accel_gate.target_roas_max != null && (
                      <> ({data.accel_gate.target_roas_min}~{data.accel_gate.target_roas_max})</>
                    )}
                  </>
                ) : (
                  <span className="text-amber-600">계정 기본값(게이트와 다른 자)</span>
                )}
                로 잰다.
              </div>
              <div className="overflow-x-auto">
                {/* ★3R P2-1 — 행 «이름»이 gate_end를 따라가는지 테스트가 볼 수 있게 자리를 고정한다.
                    testid가 없어서, 행 이름을 옛 고정 문구로 되돌려도 전건 초록이었다(생존 변이 C6). */}
                <table className="w-full text-sm" data-testid="accel-gate-buckets">
                  <thead>
                    <tr className="text-xs text-gray-500">
                      <th className="text-left font-normal pb-1">구간</th>
                      <th className="text-right font-normal pb-1">건수</th>
                      <th className="text-right font-normal pb-1">비용</th>
                      <th className="text-right font-normal pb-1">전환매출</th>
                      <th className="text-right font-normal pb-1">총이익(상한)</th>
                      <th className="text-right font-normal pb-1">총이익(하한)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {([
                      ["게이트 통과 (양끝 공통)", data.accel_gate.buckets.passing_both, false],
                      // ★2R P2-C — 행 이름이 `gate_end`를 따라야 한다. 게이트가 상한으로 옮겨진 뒤엔
                      //   이 건수를 «현행 게이트가 죽이지 않는다» — 고정 문구면 같은 카드의
                      //   헤드라인(「통과 221건」 = 195+26)과 정면으로 모순된다.
                      [data.accel_gate.gate_end === "factor_high"
                        ? "하한에서만 차단 — 현행 게이트는 통과시키는 것(하한이었다면 죽었다)"
                        : "하한에서만 차단 — 현행 게이트가 죽이는 것",
                        data.accel_gate.buckets.blocked_low_only, true],
                      ["양끝 차단", data.accel_gate.buckets.blocked_both, false],
                    ] as const).map(([label, b, emphasize]) => (
                      <tr key={label} className="border-t border-gray-100">
                        <td className={`py-1.5 pr-2 ${emphasize ? "font-medium text-gray-900" : "text-gray-600"}`}>{label}</td>
                        <td className="py-1.5 text-right tabular-nums">{num(b.count)}</td>
                        <td className="py-1.5 text-right tabular-nums text-gray-600">{won(b.cost)}</td>
                        <td className="py-1.5 text-right tabular-nums text-gray-600">{won(b.conv_amt)}</td>
                        <td className="py-1.5 text-right tabular-nums">{won(b.profit_high)}</td>
                        <td className={`py-1.5 text-right tabular-nums font-medium ${b.profit_low < 0 ? "text-red-600" : "text-gray-900"}`}>
                          {won(b.profit_low)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* ★1R P2-1: by_board를 계산·타입·테스트까지 해 놓고 화면에 한 글자도 안 그렸다 —
                  「어느 보드에서 죽는지가 안 보이면 처분을 못 정한다」고 주석에 써 놓고서다.
                  이 저장소가 네 번 밟은 「값은 있는데 사람이 못 봄」의 다섯 번째가 될 뻔했다. */}
              <div className="mt-2 text-xs text-gray-500 tabular-nums" data-testid="accel-gate-by-board">
                어디서 죽나 —{" "}
                {data.accel_gate.by_board
                  .filter((b) => b.total > 0)
                  .map((b) => `${b.board} ${b.blocked_low_only}/${b.total}`)
                  .join(" · ") || "액셀 후보 없음"}
              </div>
              <div
                className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-500 tabular-nums"
                data-testid="accel-gate-symmetry"
              >
                <span>
                  대칭(브레이크:액셀) 선정{" "}
                  <span className="font-medium text-gray-900">{data.accel_gate.ratio_selection ?? NO_DATA}:1</span>
                  {" → "}게이트 후{" "}
                  {/* ★D-NAO-234 ⓐ — 헤드라인과 같은 이유로 `gate_end`가 고른다. 여기만 옛 끝으로
                      남으면 같은 카드 안에서 두 문장이 다른 게이트를 말한다(n=40 P1-2의 모양). */}
                  {/* ★2R P1-4 — 「게이트 후」 값에 **자기 testid**를 준다. 예전엔 선정 비율과
                      같은 블록 안에 있어서, 픽스처에서 두 값이 겹치면 단언이 선정 비율로
                      만족돼 «게이트 후를 하드코딩하는 변이»(F4)를 못 잡았다. */}
                  <span className="font-medium text-gray-900" data-testid="accel-gate-ratio-after">
                    {(data.accel_gate.gate_end === "factor_high"
                      ? data.accel_gate.ratio_after_gate_high
                      : data.accel_gate.ratio_after_gate_low) ?? NO_DATA}:1
                  </span>
                  {` (${data.accel_gate.gate_end === "factor_high" ? "하한" : "상한"}이었다면 `}
                  {(data.accel_gate.gate_end === "factor_high"
                    ? data.accel_gate.ratio_after_gate_low
                    : data.accel_gate.ratio_after_gate_high) ?? NO_DATA}:1)
                </span>
                <span>브레이크 후보 {num(data.accel_gate.brake_total)}건</span>
                {/* 0이어도 «측정했더니 없음»임을 보이려고 항상 그린다 — 키 부재 ≠ 0건(교훈 #123) */}
                <span>판정 불가(roas 부재) {num(data.accel_gate.buckets.unmeasurable)}건</span>
              </div>
            </div>
          ) : null}

          {/* ① 출혈 키워드 */}
          <Board
            title="🔴 출혈 키워드"
            subtitle="WEB_SITE 등록 키워드 중 보정ROAS < 계정 BEP — 비용순"
            count={boards.bleeding_keywords.length}
          >
            <KeywordTable rows={boards.bleeding_keywords} />
          </Board>

          {/* ② 굶는 승자 */}
          <Board
            title="🌱 굶는 승자"
            subtitle="보정ROAS ≥ 목표 달성인데 일평균 클릭 < 1 — 노출 확장 후보"
            count={boards.starving_winners.length}
          >
            <KeywordTable rows={boards.starving_winners} showAvgClk />
          </Board>

          {/* ③ 확장버킷 */}
          <Board title="🧩 확장버킷" subtitle="WEB_SITE & 등록 키워드 밖 자동매칭(keyword_id='') 총계">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 p-4">
              <div>
                <div className="text-xs text-gray-500">광고비</div>
                <div className="text-sm font-semibold tabular-nums">{won(expansion.cost)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">비용 비중</div>
                <div className="text-sm font-semibold tabular-nums">{pctFromFraction(expansion.cost_share, 1)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">클릭</div>
                <div className="text-sm font-semibold tabular-nums">{num(expansion.clk)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">전환매출</div>
                <div className="text-sm font-semibold tabular-nums">{won(expansion.conv_amt)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">ROAS(보정)</div>
                <div className="text-sm font-semibold tabular-nums">{roasX(expansion.roas_corrected)}</div>
              </div>
            </div>
          </Board>

          {/* ④ 쇼핑그룹 BEP */}
          <Board
            title="🛍️ 쇼핑그룹 BEP 미달"
            subtitle="SHOPPING 캠페인 그룹(adgroup) 단위 보정ROAS < 계정 BEP — 비용순"
            count={boards.shopping_group_bep.length}
          >
            {boards.shopping_group_bep.length === 0 ? <EmptyRow /> : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-200">
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">캠페인/그룹</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">광고비</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">전환매출</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">ROAS(보정)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {boards.shopping_group_bep.map((r, i) => (
                      <tr key={`${r.campaign_id}/${r.adgroup_id}#${i}`} className="hover:bg-gray-50">
                        <td className="px-4 py-2 text-sm border-b border-gray-100">{r.campaign_id} / {r.adgroup_id}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.cost)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.conv_amt)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums font-medium">{roasX(r.roas_corrected)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Board>

          {/* ⑤ 제외후보 */}
          <Board
            title="🚫 제외 후보 검색어"
            subtitle="확장버킷 검색어 중 비용 상위 — 전환은 검색어 단위 미추적(정직 경계), 최종 판단은 승격 후 실측"
            count={boards.exclusion_candidates.length}
          >
            {boards.exclusion_candidates.length === 0 ? <EmptyRow /> : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-200">
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">검색어</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">캠페인/그룹</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">소스</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">노출</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">클릭</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">광고비</th>
                    </tr>
                  </thead>
                  <tbody>
                    {boards.exclusion_candidates.map((r, i) => (
                      <tr key={`${r.campaign_id}/${r.adgroup_id}/${r.search_term}#${i}`} className="hover:bg-gray-50">
                        <td className="px-4 py-2 text-sm border-b border-gray-100">{r.search_term}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-gray-500">{r.campaign_id} / {r.adgroup_id}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-gray-500">{r.source}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.imp)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.clk)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Board>

          {/* ⑥ 3단분류 */}
          <Board title="🧹 키워드 위생 3단분류" subtitle="판정가능(최근30일 클릭≥10) / 육성후보(저클릭+검색량 있음) / 진짜정리(저클릭+검색량 없음)">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 p-4">
              <div>
                <div className="text-xs text-gray-500">전체(on)</div>
                <div className="text-lg font-semibold tabular-nums">{num(triage.total)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">판정가능</div>
                <div className="text-lg font-semibold tabular-nums text-blue-600">{num(triage.judgeable)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">육성후보</div>
                <div className="text-lg font-semibold tabular-nums text-green-600">{num(triage.growth_candidate)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">진짜정리</div>
                <div className="text-lg font-semibold tabular-nums text-red-600">{num(triage.dead)}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">검색량 미조회</div>
                <div className="text-lg font-semibold tabular-nums text-gray-400">{num(triage.volume_unchecked)}</div>
              </div>
            </div>
          </Board>

          {/* ⑦ 악순환 */}
          <Board
            title="🔻 악순환 감지"
            subtitle="최근 7일 보정ROAS 하락(전 23일 대비 10%↓) + 클릭 위축(30%↓) + 목표 미달 지속 — 예산소진율 미연결(대리신호)"
            count={boards.vicious_cycle.length}
          >
            {boards.vicious_cycle.length === 0 ? <EmptyRow /> : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-200">
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">캠페인</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">최근7일 ROAS(보정)</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">이전23일 ROAS(보정)</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">최근 일평균클릭</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">이전 일평균클릭</th>
                    </tr>
                  </thead>
                  <tbody>
                    {boards.vicious_cycle.map((r, i) => (
                      <tr key={`${r.campaign_id}#${i}`} className="hover:bg-gray-50">
                        <td className="px-4 py-2 text-sm border-b border-gray-100">{r.campaign_id}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums text-red-600 font-medium">{roasX(r.recent_roas_corrected)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums text-gray-500">{roasX(r.prior_roas_corrected)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{r.recent_daily_clk.toFixed(2)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums text-gray-500">{r.prior_daily_clk.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Board>

          {/* ⑧ 바닥 대기 (UI3, D-NAO-65 — 관찰 전용) */}
          <Board
            title="⏸️ 바닥 대기 (관찰 전용 · 실행 없음)"
            subtitle="실효입찰이 하한이라 더 못 내리는데 pause 예외(ML·레버끊김·지속밸브)도 아닌 유닛 — 어떤 제안도 생성되지 않고 바닥에서 대기 중. 쇼핑=전환有 바닥손실(레버정상) / 파워링크=무전환 at-floor."
            count={boards.floor_wait_units?.length ?? 0}
          >
            {!boards.floor_wait_units || boards.floor_wait_units.length === 0 ? (
              <EmptyRow text="바닥 대기 중인 유닛 없음" />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-200">
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">유형</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">캠페인/유닛</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">실효입찰</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">클릭</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">누적비용</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">스톱로스임계</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-right">전환</th>
                      <th className="px-4 py-2 text-xs font-medium text-gray-500 text-left">대기 사유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {boards.floor_wait_units.map((r, i) => (
                      <tr key={`${r.campaign_id}/${r.adgroup_id}/${r.keyword_id ?? ""}#${i}`} className="hover:bg-gray-50">
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-gray-500">
                          {r.target_type === "keyword" ? "파워링크" : "쇼핑"}
                        </td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100">{floorWaitLabel(r)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.effective_bid)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{num(r.clk)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">{won(r.cost)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums text-gray-500">{won(r.stop_loss_amount)}</td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-right tabular-nums">
                          {r.has_conv ? won(r.conv_amt) : NO_DATA}
                        </td>
                        <td className="px-4 py-2 text-sm border-b border-gray-100 text-gray-600">{r.reason_label}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Board>
        </>
      ) : (
        <EmptyRow text="데이터가 없습니다" />
      )}
    </div>
  );
}
