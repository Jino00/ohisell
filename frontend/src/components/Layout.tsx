// Layout.tsx — 사이드바 + 메인 영역 레이아웃
// 데스크탑: 고정 사이드바. 모바일(<md): 햄버거 → 슬라이드 드로어.
// 대시보드(전체)를 부모 메뉴로 두고, 채널별 운영(쿠팡·스마트스토어)을 접이식 자식으로 묶음.
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import SchedulerStatus from "./SchedulerStatus";

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
  { to: "/inventory", label: "재고 관리", icon: "🏭" },
  { to: "/settlements", label: "정산 관리", icon: "💰" },
  { to: "/ad-report", label: "광고 리포트", icon: "📈" },
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

  // 채널 페이지로 직접 진입하면 그룹을 자동으로 펼침
  useEffect(() => {
    if (childActive) setOpen(true);
  }, [childActive]);

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
        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
