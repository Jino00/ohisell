// Layout.tsx — 사이드바 + 메인 영역 레이아웃
// 데스크탑: 고정 사이드바. 모바일(<md): 햄버거 → 슬라이드 드로어.
// 대시보드(전체)를 부모 메뉴로 두고, 채널별 운영(쿠팡·스마트스토어)을 접이식 자식으로 묶음.
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import SchedulerStatus from "./SchedulerStatus";
import { getAdCostCookieStatus, requestAdCostRefresh, type AdCostCookieStatus } from "../lib/api";

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

  // 경로 이동 시 모바일 드로어 닫기
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

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

        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
