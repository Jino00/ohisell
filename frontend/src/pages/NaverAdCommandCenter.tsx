// NaverAdCommandCenter.tsx — D-NAO-47 1층. 우리 MOP가 돌리는 광고의 성과.
//
// D-47-a: 1층은 "우리 MOP가 돌리는 광고의 성과"다(Jino: "우리 MOP가 돌리는 광고성과를 보자는거야").
// D-47-c: N=1(오늘 04 카나리 1개)과 N=여럿(나중)이 **같은 컴포넌트**다 — 카나리 전용 화면 금지.
// D-47-h: **"왜 0인가"가 1층 시민**이다. 라이브 실측상 이 화면은 대부분 0으로 채워진다
//         (커버리지 1.15% · 우리 조작 0회 · 승인 0건). 0을 찍고 침묵하면 MOP의 실패를
//         복제하는 것이다(스펙 §2-3). 볼품없어도 그게 사실이다.
//
// ★계획 대비 실측 정정(P2-T7): 계획서는 `fetchNaverAdDashboardOverview`·`fetchNaverCampaignSettings`
// 를 가정했으나 api.ts의 실제 함수명은 `getNaverDashboardOverview`(campaign-settings 쪽은 계획과
// 이름이 일치했음). optimizer_coverage의 총합 필드명도 `total`이 아니라 `total_cost`다
// (NaverDashboardOptimizerCoverage, api.ts:1856). NaverAdCampaignSettings에는 campaign_name이
// 없어(백엔드 _serialize_settings가 안 줌) campaign_id만 표시한다 — 추측으로 필드를 만들지 않는다.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Stat, EmptyState, Loading, CoverageBar, Table, Th, Td, Badge, LayerNav } from "../components/ui";
import { num, won, pctFromFraction } from "../lib/format";
import {
  getNaverDashboardOverview, fetchNaverChangeLog, fetchNaverCampaignSettings,
  type NaverDashboardOverview, type NaverAdCampaignSettings,
} from "../lib/api";

export default function NaverAdCommandCenter() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<NaverDashboardOverview | null>(null);
  const [changeCount, setChangeCount] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        setLoading(true);
        const [ov, cl] = await Promise.all([
          getNaverDashboardOverview(),
          // ★dry_run 제외가 기본 — 실집행만 센다. 아무것도 안 했는데 일한 것처럼
          //   보이면 안 된다(D-47-h 정직성).
          fetchNaverChangeLog({ days: 30, limit: 1 }),
        ]);
        if (!alive) return;
        setOverview(ov);
        setChangeCount(cl.total);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  if (loading) return <><LayerNav /><Loading label="커맨드 센터를 불러오는 중…" rows={6} /></>;
  if (error) return <><LayerNav /><EmptyState reason={`불러오지 못했습니다: ${error}`} hint="새로고침하거나 백엔드 상태를 확인하세요." /></>;

  const cov = overview?.optimizer_coverage ?? { window_days: 7, ours_cost: 0, mop_cost: 0, none_cost: 0, total_cost: 0, ours_ratio: 0 };

  return (
    <div className="space-y-4">
      <LayerNav />
      {/* ① 관리주체 3열 대조 — 우리 열만 크게(위계=대비) */}
      <Card title="누가 이 광고를 돌리는가">
        <div className="p-4 space-y-4">
          <CoverageBar ours={cov.ours_cost} mop={cov.mop_cost} manual={cov.none_cost} />
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded border border-blue-200 bg-blue-50/40 p-3">
              <Badge tone="owner">우리 MOP</Badge>
              <div className="mt-2">
                <Stat
                  label="광고비"
                  value={won(cov.ours_cost)}
                  isEmpty={cov.ours_cost === 0}
                  reason="아직 우리 MOP에 넘긴 캠페인이 없습니다."
                  tone={cov.ours_cost === 0 ? "idle" : "neutral"}
                  sub={cov.total_cost ? `전체의 ${pctFromFraction(cov.ours_cost / cov.total_cost)}` : undefined}
                />
              </div>
            </div>
            <div className="rounded border border-gray-200 p-3">
              <Badge>원본 MOP</Badge>
              <div className="mt-2">
                <Stat
                  label="광고비"
                  value={won(cov.mop_cost)}
                  isEmpty={cov.mop_cost === 0}
                  // ★D-47-g: 03을 optimizer='mop'으로 태깅해야 이 열이 채워진다.
                  reason="원본 MOP가 돌리는 캠페인이 optimizer='mop'으로 태깅되지 않았습니다(D-47-g)."
                  tone="idle"
                />
              </div>
            </div>
            <div className="rounded border border-gray-200 p-3">
              <Badge>수동</Badge>
              <div className="mt-2">
                <Stat label="광고비" value={won(cov.none_cost)} tone="neutral" />
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* ② 우리 MOP 캠페인 리스트 — 오늘 1행, 나중 N행(D-47-c) */}
      <Card
        title="우리 MOP가 돌리는 캠페인"
        right={<Link to="/naver-ad/console" className="text-xs text-blue-600 hover:underline">캠페인 넘기기 →</Link>}
      >
        <OursCampaignList changeCount={changeCount} />
      </Card>
    </div>
  );
}

function OursCampaignList({ changeCount }: { changeCount: number | null }) {
  const [rows, setRows] = useState<NaverAdCampaignSettings[] | null>(null);

  useEffect(() => {
    let alive = true;
    fetchNaverCampaignSettings()
      .then((r) => { if (alive) setRows(r.rows.filter((c) => c.optimizer === "ours")); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, []);

  if (rows === null) return <Loading rows={2} />;
  if (rows.length === 0) {
    return (
      <EmptyState
        reason="우리 MOP에 넘긴 캠페인이 아직 없습니다."
        hint="최적화 콘솔에서 캠페인의 관리 주체를 '우리'로 바꾸면 여기에 나타납니다."
      />
    );
  }

  return (
    <Table head={<>
      <Th>캠페인</Th>
      <Th right>우리 조작</Th>
      <Th>상태</Th>
    </>}>
      {rows.map((c) => (
        <tr key={c.campaign_id}>
          {/* ★campaign_name은 백엔드 /campaign-settings가 주지 않는다(_serialize_settings 실측) —
              추측으로 필드를 만들지 않고 campaign_id만 표시한다. */}
          <Td><span className="text-gray-900">{c.campaign_id}</span></Td>
          {/* ★"우리 조작 N회" — 프로그램이 일하는지 매일 보이는 자리.
              0은 회색(judge-idle)이다. 빨강이면 고장난 것처럼 보이고 초록이면 거짓말이다. */}
          <Td right>
            <span className={changeCount === 0 ? "text-judge-idle" : "text-gray-900"}>
              {changeCount == null ? "—" : `${num(changeCount)}회`}
            </span>
          </Td>
          <Td>
            {changeCount === 0 ? (
              <span className="text-xs text-gray-500">
                제안은 나오지만 승인된 실행이 없습니다(사람 승인 게이트 대기).
              </span>
            ) : <Badge tone="owner">가동 중</Badge>}
          </Td>
        </tr>
      ))}
    </Table>
  );
}
