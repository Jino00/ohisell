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
              <div className="text-xs text-gray-400 mt-0.5">
                {data.correction_factor.source === "actual_revenue_ratio"
                  ? `${data.correction_factor.window_from}~${data.correction_factor.window_to} · 점추정 ×${data.correction_factor.factor_point.toFixed(4)}`
                  : "산출 불가(1.0 폴백)"}
              </div>
              <div className="text-[11px] text-gray-400 mt-1 leading-snug">
                하한=보정 없음 · 상한=채널매출÷광고전환매출(광고 귀속 조인 없음 ={" "}
                <span className="text-gray-500">100% 견인 가정</span>).{" "}
                <span className="text-gray-500">아래 보드의 후보 «선정»은 전부 상한</span>이고(액셀
                판정 불변), 하한은 실제 쓰기의 «크기»에만 쓴다 — 입찰 크기·증액 가드·확장 배분.
              </div>
            </div>
            <div className="bg-white rounded-lg border border-gray-200 p-3">
              <div className="text-xs text-gray-500 mb-1">진단 창</div>
              <div className="text-sm font-medium text-gray-900">{data.window.date_from} ~ {data.window.date_to}</div>
            </div>
          </div>

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
