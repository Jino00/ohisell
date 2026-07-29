// LayerNav.tsx — D-NAO-47. 탭을 URL로(§8-4). 딥링크·북마크·새로고침 복원이 되어야 한다.
import { NavLink } from "react-router-dom";

const LINKS = [
  // ★"성과"가 맨 앞이다(D-NAO-104 계획서 §5) — 사장님 뷰가 첫 탭. 나머지는 운영자 화면이다.
  { to: "/naver-ad/performance", label: "성과" },
  { to: "/naver-ad", label: "커맨드 센터", end: true },
  { to: "/naver-ad/report", label: "리포트" },
  { to: "/naver-ad/diagnosis", label: "진단 보드" },
  { to: "/naver-ad/console", label: "최적화 콘솔" },
  { to: "/naver-ad/raw", label: "원자료" },
];

export function LayerNav() {
  return (
    <nav className="flex gap-1 border-b border-gray-200 mb-4">
      {LINKS.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) =>
            `px-3 py-2 text-sm border-b-2 -mb-px ${
              isActive ? "border-blue-600 text-blue-700 font-medium" : "border-transparent text-gray-500 hover:text-gray-800"
            }`
          }
        >
          {l.label}
        </NavLink>
      ))}
    </nav>
  );
}
