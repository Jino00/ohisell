// Layout.tsx — 사이드바 + 메인 영역 레이아웃
import { NavLink, Outlet } from "react-router-dom";
import SchedulerStatus from "./SchedulerStatus";

const NAV_ITEMS = [
  { to: "/", label: "대시보드", icon: "📊" },
  { to: "/command-center", label: "종합 조망", icon: "🎯" },
  { to: "/orders", label: "주문 관리", icon: "📋" },
  { to: "/products", label: "상품 관리", icon: "📦" },
  { to: "/inventory", label: "재고 관리", icon: "🏭" },
  { to: "/settlements", label: "정산 관리", icon: "💰" },
  { to: "/ad-report", label: "광고 리포트", icon: "📈" },
  { to: "/settings", label: "설정", icon: "⚙️" },
];

export default function Layout() {
  return (
    <div className="flex h-screen bg-gray-50">
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-4 border-b border-gray-200">
          <h1 className="text-lg font-bold text-gray-900">ohisell</h1>
          <p className="text-xs text-gray-500">오픈쇼핑몰 실적 관리</p>
        </div>
        <nav className="flex-1 p-2">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2 px-3 py-2 rounded-md text-sm mb-1 ${
                  isActive
                    ? "bg-blue-50 text-blue-700 font-medium"
                    : "text-gray-600 hover:bg-gray-100"
                }`
              }
            >
              <span>{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <SchedulerStatus />
      </aside>
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
