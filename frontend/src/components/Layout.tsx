// Layout.tsx — 사이드바 + 메인 영역 레이아웃
// 데스크탑: 고정 사이드바. 모바일(<md): 햄버거 → 슬라이드 드로어.
// 대시보드(전체)를 부모 메뉴로 두고, 채널별 운영(쿠팡·스마트스토어)을 접이식 자식으로 묶음.
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import SchedulerStatus from "./SchedulerStatus";
import {
  getAdCostCookieStatus,
  requestAdCostRefresh,
  getSchedulerHealth,
  getCollectionStatus,
  type AdCostCookieStatus,
  type SchedulerHealth,
  type CollectionStatus,
} from "../lib/api";
import { buildCollectionFreshnessBanner } from "./collectionFreshnessBanner";
import { runStreamsRefresh, describeOutcome, specsForKeys } from "../lib/streamRefresh";

// 전역 헬스 배너 요약 빌더 (순수 함수 — 테스트 대상).
// healthy:false여도 실제로 표시할 문제가 없으면 null 반환(배너 숨김). 규칙:
//  - COUPANG_ADS1 쿠키 항목은 제외(쿠팡 광고비 배너가 전담) → 제외 후 0개면 null.
//  - disabled 버킷은 정상(의도적 비활성)이므로 문제로 세지 않음.
//  - scheduler_running===false는 최우선 표기.
// 반환: summary=" · "로 이은 한 줄(배너 truncate용), detail=줄바꿈 목록(title 호버용).
export function buildPipelineHealthBanner(
  health: SchedulerHealth,
): { summary: string; detail: string } | null {
  if (health.healthy) return null;
  const parts: string[] = [];

  // 1) 스케줄러 자체 정지 — 최우선
  if (health.scheduler_running === false) {
    parts.push("스케줄러 정지");
  }

  // 2) 쿠키 만료 — COUPANG_ADS1은 광고비 배너 전담이므로 제외
  for (const c of health.cookies_stale ?? []) {
    if (c.account_key === "COUPANG_ADS1") continue;
    const days = c.age_days != null ? ` (${Math.floor(c.age_days)}일째)` : "";
    let label: string;
    if (c.account_key === "COUPANG_WING1") {
      label = "RG 정산 수집 중단(오픽스) — 쿠키 재등록 필요";
    } else if (c.account_key === "COUPANG_WING2") {
      label = "RG 정산 수집 중단(오하이테크) — 쿠키 재등록 필요";
    } else {
      label = `${c.account_key} 쿠키 만료`;
    }
    parts.push(label + days);
  }

  // 3) 데이터 나이 — 백엔드 impact 라벨 그대로 노출
  for (const d of health.data_stale ?? []) {
    const days = d.age_days != null ? ` (${Math.floor(d.age_days)}일째)` : "";
    parts.push(`${d.impact}${days}`);
  }

  // 4) 잡 문제 (disabled 제외 — 정상)
  const jobNames: string[] = [
    ...(health.failed ?? []).map((j) => j.job_name),
    ...(health.stale ?? []).map((j) => j.job_name),
    ...(health.never_succeeded ?? []).map((j) => j.job_name),
    ...(health.missing_jobs ?? []),
  ];
  for (const n of jobNames) {
    parts.push(`잡 실패: ${n}`);
  }

  if (parts.length === 0) return null;
  return { summary: parts.join(" · "), detail: parts.join("\n") };
}

// 대시보드 하위 채널별 운영 패널 (접이식)
const DASHBOARD_CHILDREN = [
  { to: "/coupang-ops", label: "쿠팡 운영", icon: "🔧" },
  { to: "/naver-ops", label: "스마트스토어", icon: "🛒" },
];

// 대시보드 그룹 다음에 오는 최상위 메뉴들
const NAV_ITEMS = [
  { to: "/command-center", label: "종합 조망", icon: "🎯" },
  { to: "/orders", label: "주문 관리", icon: "📋" },
  { to: "/products", label: "상품 관리", icon: "📦" },
  { to: "/product-connection-map", label: "상품 연결맵", icon: "🔗" },
  { to: "/rocket-recon", label: "로켓 발주·정산 대사", icon: "🚀" },
  { to: "/inventory", label: "재고 관리", icon: "🏭" },
  { to: "/settlements", label: "정산 관리", icon: "💰" },
  { to: "/ad-report", label: "광고 리포트", icon: "📈" },
  { to: "/naver-ad", label: "네이버 광고", icon: "🟢" },
  { to: "/settings", label: "설정", icon: "⚙️" },
];

function linkClass({ isActive }: { isActive: boolean }) {
  return `flex items-center gap-2 px-3 py-2 rounded-md text-sm mb-1 ${
    isActive ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-600 hover:bg-gray-100"
  }`;
}

export default function Layout() {
  const location = useLocation();
  const childActive = DASHBOARD_CHILDREN.some((c) => location.pathname === c.to);
  const [open, setOpen] = useState(childActive || location.pathname === "/");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [adCookie, setAdCookie] = useState<AdCostCookieStatus | null>(null);
  const [adRefreshing, setAdRefreshing] = useState(false);
  const [adRefreshMsg, setAdRefreshMsg] = useState<string | null>(null);
  const [health, setHealth] = useState<SchedulerHealth | null>(null);
  const [collection, setCollection] = useState<CollectionStatus | null>(null);
  // 수집 신선도 배너 '지금 갱신' 상태 — 2026-08-03까지 이 자리는 링크였고, 눌러도 페이지만
  // 바뀌어 "아무 일도 안 일어난다"로 보였다(Jino 보고). 이제 실제 갱신을 돌린다.
  const [collRefreshing, setCollRefreshing] = useState(false);
  const [collRefreshMsg, setCollRefreshMsg] = useState<string | null>(null);
  // 갱신은 최대 215초 걸린다 — 그 사이 언마운트되거나 사용자가 다시 누를 수 있다. 마운트
  // 플래그 + 실행 세대로 늦은 콜백의 setState를 막고, 소거 타이머도 정리한다(codex R1[P2]).
  const collMounted = useRef(true);
  const collRunSeq = useRef(0);
  const collClearTimer = useRef<number | null>(null);
  useEffect(() => {
    collMounted.current = true;
    return () => {
      collMounted.current = false;
      if (collClearTimer.current !== null) window.clearTimeout(collClearTimer.current);
    };
  }, []);

  // 배너 '지금 갱신' — stale(쿠키 정상, Mac 페처 지연)일 때 실제 갱신 요청.
  // request_refresh 플래그 set → Mac 데몬이 다음 폴링(~20초)에서 fetch·push. 12초 뒤 상태 재확인.
  async function handleAdRefresh() {
    setAdRefreshing(true);
    setAdRefreshMsg(null);
    try {
      await requestAdCostRefresh();
      setAdRefreshMsg("갱신 요청됨 — Mac 페처가 켜져 있으면 ~20초 후 반영됩니다.");
      setTimeout(() => {
        getAdCostCookieStatus().then(setAdCookie).catch(() => { /* 무시 */ });
      }, 12000);
    } catch {
      setAdRefreshMsg("갱신 요청 실패 — 쿠키 재설정이 필요할 수 있습니다.");
    } finally {
      setAdRefreshing(false);
    }
  }

  // 채널 페이지로 직접 진입하면 그룹을 자동으로 펼침
  useEffect(() => {
    if (childActive) setOpen(true);
  }, [childActive]);

  // 광고쿠키 만료 감지 — 페이지 진입/이동마다 재확인.
  // 접속 시 realtime sync가 만료(302)를 감지해 status를 red로 바꾸므로, 6초 뒤 한 번 더 확인.
  useEffect(() => {
    let cancelled = false;
    const fetchStatus = () => {
      getAdCostCookieStatus()
        .then((s) => { if (!cancelled) setAdCookie(s); })
        .catch(() => { /* 조용히 실패 — 배너만 미표시 */ });
    };
    fetchStatus();
    const t = setTimeout(fetchStatus, 6000);
    return () => { cancelled = true; clearTimeout(t); };
  }, [location.pathname]);

  // 파이프라인 헬스 — 전역 5분 폴링(경로와 무관하게 상주).
  // 실패 시 조용히 무시 → 네트워크 오류로 배너를 잘못 띄우지 않음(오탐 금지).
  useEffect(() => {
    let cancelled = false;
    const fetchHealth = () => {
      getSchedulerHealth()
        .then((h) => { if (!cancelled) setHealth(h); })
        .catch(() => { /* 조용히 실패 — 배너 미표시 */ });
    };
    fetchHealth();
    const t = setInterval(fetchHealth, 5 * 60 * 1000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  // 쿠팡 수집 신선도 — 전역 60초 폴링. 자동 트리거 제거 후 '낡음/실패'를 여기서만 가시화.
  // 실패 시 조용히 무시(fail-safe) → 네트워크 오류로 배너를 잘못 띄우지 않고 앱도 안 죽는다.
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      getCollectionStatus()
        .then((c) => { if (!cancelled) setCollection(c); })
        .catch(() => { /* 조용히 실패 — 배너 미표시 */ });
    };
    tick();
    const t = setInterval(tick, 60 * 1000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  // 배너 '지금 갱신' — 낡은/실패한 스트림 전부를 한 번에 갱신한다.
  //  ★계정별 개별 선택은 커맨드센터가 담당한다(같은 UI를 배너에 복제하지 않는다 — 한쪽만
  //    고쳐지는 사고를 만들지 않기 위해). 배너의 일은 "배너를 없애는 것"이므로 전건 갱신이다.
  //  ★로그인이 필요한 스트림은 계정명과 함께 남긴다 — 로그인은 버튼이 대신할 수 없고,
  //    어느 계정인지 모르면 창을 찾지 못한다(2026-08-03 실측: 4개 중 2개가 rc=3 로그인 필요).
  async function handleCollectionRefresh(keys: string[]) {
    const { specs, unknown } = specsForKeys(keys);
    // ★못 알아본 key를 침묵시키지 않는다(codex R1[P2]): 백엔드가 스트림을 추가·개명하면
    //   매칭이 0건이 되는데, 조용히 return하면 버튼이 다시 "눌러도 아무 일 없는" 물건이 된다.
    const unknownNote = unknown.length ? `⚠️ 미지원 항목 ${unknown.join(", ")}(갱신 불가)` : "";
    if (specs.length === 0) {
      setCollRefreshMsg(unknownNote || "⚠️ 갱신할 수 있는 항목이 없습니다");
      return;
    }
    // 실행 세대 — 이전 실행의 늦은 콜백이 새 실행의 문구를 덮어쓰는 것을 막는다(codex R1[P2]).
    const gen = ++collRunSeq.current;
    const alive = () => collMounted.current && gen === collRunSeq.current;
    const withNote = (body: string) => [body, unknownNote].filter(Boolean).join(" · ");

    setCollRefreshing(true);
    setCollRefreshMsg(withNote(specs.map((s) => describeOutcome(s, null)).join(" · ")));
    try {
      const results = await runStreamsRefresh(specs, (_spec, _outcome, all) => {
        // 한 건이 정착할 때마다 문구를 갱신 — 느린 스트림이 빠른 스트림을 인질로 잡지 않는다.
        if (!alive()) return;
        setCollRefreshMsg(
          withNote(specs.map((s) => describeOutcome(s, all.get(s.key) ?? null)).join(" · ")),
        );
      });
      if (!alive()) return;
      const allDone = specs.every((s) => results.get(s.key)?.state === "done") && !unknown.length;
      // 완료분을 배너에서 즉시 걷어내기 위해 신선도를 다시 읽는다(60초 폴링을 기다리지 않음).
      // ★재조회 실패를 삼키지 않는다(codex R1[P2]): 삼키면 문구만 사라지고 낡은 배너가 이유
      //   없이 되돌아온다 — 갱신이 실패한 것처럼 보인다. 실패하면 문구를 남기고 이유를 적는다.
      getCollectionStatus()
        .then((c) => {
          if (!alive()) return;
          setCollection(c);
          // 전건 성공일 때만 문구 자동 소거 — 실패/로그인 필요는 남겨서 Jino가 보게 한다.
          if (allDone) {
            collClearTimer.current = window.setTimeout(() => {
              if (alive()) setCollRefreshMsg(null);
            }, 4000);
          }
        })
        .catch(() => {
          if (!alive()) return;
          setCollRefreshMsg((prev) => `${prev ?? ""} · ⚠️ 상태 재조회 실패(잠시 후 자동 갱신)`);
        });
    } catch (e: any) {
      if (alive()) setCollRefreshMsg("❌ 갱신 요청 실패: " + (e?.message || ""));
    } finally {
      if (alive()) setCollRefreshing(false);
    }
  }

  // 경로 이동 시 모바일 드로어 닫기
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  const healthBanner = health ? buildPipelineHealthBanner(health) : null;
  const collectionBanner = buildCollectionFreshnessBanner(collection);

  return (
    <div className="flex h-screen bg-gray-50">
      {/* 모바일 드로어 배경 */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/30 z-30 md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      {/* 사이드바: 데스크탑 고정 / 모바일 슬라이드 드로어 */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-64 bg-white border-r border-gray-200 flex flex-col
          transform transition-transform duration-200 md:static md:w-56 md:translate-x-0 md:z-auto
          ${mobileOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="p-4 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-gray-900">ohisell</h1>
            <p className="text-xs text-gray-500">오픈쇼핑몰 실적 관리</p>
          </div>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            className="md:hidden text-gray-400 hover:text-gray-700 text-xl leading-none px-1"
            aria-label="메뉴 닫기"
          >
            ✕
          </button>
        </div>
        <nav className="flex-1 p-2 overflow-y-auto">
          {/* 대시보드(전체) — 클릭=전체 종합, ▾로 채널별 운영 펼침/접기 */}
          <div className="flex items-center mb-1">
            <NavLink
              to="/"
              end
              className={({ isActive }) =>
                `flex-1 flex items-center gap-2 px-3 py-2 rounded-md text-sm ${
                  isActive ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-600 hover:bg-gray-100"
                }`
              }
            >
              <span>📊</span>
              대시보드
            </NavLink>
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-label={open ? "채널 메뉴 접기" : "채널 메뉴 펼치기"}
              aria-expanded={open}
              className="px-2 py-2 text-gray-400 hover:text-gray-700 text-xs"
            >
              {open ? "▾" : "▸"}
            </button>
          </div>
          {open && (
            <div className="ml-3 border-l border-gray-200 pl-2 mb-1">
              {DASHBOARD_CHILDREN.map((c) => (
                <NavLink key={c.to} to={c.to} className={linkClass}>
                  <span>{c.icon}</span>
                  {c.label}
                </NavLink>
              ))}
            </div>
          )}

          {/* 나머지 최상위 메뉴 */}
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} className={linkClass}>
              <span>{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <SchedulerStatus />
      </aside>

      {/* 메인 영역 */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 모바일 상단바 (햄버거) */}
        <header className="md:hidden flex items-center gap-3 bg-white border-b border-gray-200 px-4 py-3 sticky top-0 z-20">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="text-gray-700 text-2xl leading-none"
            aria-label="메뉴 열기"
          >
            ☰
          </button>
          <span className="text-base font-bold text-gray-900">ohisell</span>
        </header>

        {/* 광고쿠키 수집 중단 전역 경고 — 어느 페이지에 있든 보임 (광고비 stale 방지) */}
        {/* 크론 꺼짐 > red > stale 우선순위: 크론이 꺼져 있으면 쿠키가 멀쩡해도 push가 안 와 재설정은 헛수고 */}
        {(adCookie?.refresh_cron_enabled === false || adCookie?.status === "red" || adCookie?.stale) && (
          <div className="flex items-center gap-3 bg-red-600 text-white px-4 py-2 text-sm">
            <span className="font-semibold shrink-0">🔴 쿠팡 광고비 수집 중단</span>
            {adCookie?.refresh_cron_enabled === false ? (
              <>
                <span className="text-red-100 min-w-0 truncate">
                  갱신 크론 꺼짐(스케줄러에서 재개 필요)
                  {adCookie.last_success_at && ` (마지막 수집 ${adCookie.last_success_at.slice(0, 10)})`}.
                </span>
                <Link
                  to="/coupang-ops"
                  className="ml-auto shrink-0 bg-white text-red-700 font-medium px-3 py-1 rounded hover:bg-red-50"
                >
                  스케줄러 관리 →
                </Link>
              </>
            ) : (
              <>
                <span className="text-red-100 min-w-0 truncate">
                  {adRefreshMsg ?? (
                    <>
                      광고비 수집이 멈췄습니다 — {adCookie?.status === "red" ? "쿠키 만료(재설정 필요)" : "로컬 페처 확인 필요"}
                      {adCookie?.last_success_at && ` (마지막 수집 ${adCookie.last_success_at.slice(0, 10)})`}.
                    </>
                  )}
                </span>
                {adCookie?.status === "red" ? (
                  // 쿠키 만료 → 재설정 폼으로 (Mac이 fetch해도 인증 실패하므로 갱신 요청 무의미)
                  <Link
                    to="/coupang-ops?adcookie=open"
                    className="ml-auto shrink-0 bg-white text-red-700 font-medium px-3 py-1 rounded hover:bg-red-50"
                  >
                    쿠키 다시 설정 →
                  </Link>
                ) : (
                  // stale(쿠키 정상, 페처 지연) → 실제 갱신 요청
                  <button
                    onClick={handleAdRefresh}
                    disabled={adRefreshing}
                    className="ml-auto shrink-0 bg-white text-red-700 font-medium px-3 py-1 rounded hover:bg-red-50 disabled:opacity-60"
                  >
                    {adRefreshing ? "요청 중…" : "지금 갱신 →"}
                  </button>
                )}
              </>
            )}
          </div>
        )}

        {/* 파이프라인 헬스 전역 경고 — /api/scheduler/health의 healthy:false 표면화.
            광고비 배너(위)와 별개·동시 표시 가능. COUPANG_ADS1 쿠키는 위 배너 전담이라 제외됨. */}
        {healthBanner && (
          <div
            className="flex items-center gap-3 bg-amber-600 text-white px-4 py-2 text-sm"
            title={healthBanner.detail}
          >
            <span className="font-semibold shrink-0">⚠️ 파이프라인 경고</span>
            <span className="text-amber-100 min-w-0 truncate">{healthBanner.summary}</span>
            {/* ★이 자리는 '액션'이 아니라 '이동'이다 — 여기 모이는 문제(쿠키 재등록·잡 실패)는
                버튼 한 번으로 해결되지 않고 각자 다른 처방이 필요하다. 그래서 갱신 버튼을 달지
                않고 라벨을 이동으로 정직하게 적는다(2026-08-03: 구 라벨 '확인 →'이 눌리는
                버튼처럼 보여 "눌러도 아무 일이 없다"는 오해를 만들었다). */}
            <Link
              to="/coupang-ops"
              className="ml-auto shrink-0 bg-white text-amber-700 font-medium px-3 py-1 rounded hover:bg-amber-50"
            >
              쿠팡 운영 열기 →
            </Link>
          </div>
        )}

        {/* 쿠팡 수집 신선도 전역 배너 — 자동 트리거 제거(순수 on-demand) 후 '낡음/실패'를 표면화.
            빨강=48h↑ 낡음 or 갱신 실패(로그인 필요), 노랑=24~48h 낡음. 항목 클릭 → 종합조망에서 갱신. */}
        {collectionBanner && (
          <div
            className={`flex items-center gap-3 px-4 py-2 text-sm text-white ${
              collectionBanner.severity === "red" ? "bg-rose-600" : "bg-amber-500"
            }`}
          >
            <span className="font-semibold shrink-0">🕒 수집 신선도</span>
            <span className="min-w-0 truncate" title={collRefreshMsg ?? undefined}>
              {collRefreshMsg ?? collectionBanner.items.map((it, i) => (
                <span key={it.key}>
                  {i > 0 && " · "}
                  {it.text}
                </span>
              ))}
            </span>
            <button
              onClick={() => handleCollectionRefresh(collectionBanner.items.map((it) => it.key))}
              disabled={collRefreshing}
              className={`ml-auto shrink-0 bg-white font-medium px-3 py-1 rounded hover:bg-gray-50 disabled:opacity-60 ${
                collectionBanner.severity === "red" ? "text-rose-700" : "text-amber-700"
              }`}
            >
              {collRefreshing ? "갱신 중…" : "지금 갱신 →"}
            </button>
            <Link
              to="/command-center"
              className="shrink-0 underline decoration-white/50 hover:decoration-white text-xs opacity-90"
            >
              개별 갱신
            </Link>
          </div>
        )}

        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
