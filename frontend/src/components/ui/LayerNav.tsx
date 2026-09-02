// LayerNav.tsx — D-NAO-47. 탭을 URL로(§8-4). 딥링크·북마크·새로고침 복원이 되어야 한다.
import { NavLink } from "react-router-dom";

const LINKS = [
  // ★"성과"가 맨 앞이다(D-NAO-104 계획서 §5) — 사장님 뷰가 첫 탭. 나머지는 운영자 화면이다.
  { to: "/naver-ad/performance", label: "성과" },
  { to: "/naver-ad", label: "커맨드 센터", end: true },
  { to: "/naver-ad/report", label: "리포트" },
  { to: "/naver-ad/diagnosis", label: "진단 보드" },
  { to: "/naver-ad/console", label: "최적화 콘솔" },
  // ★설계서 §7½ 1단계 「도달과 이름」 — 이 둘은 라우트·화면이 **이미 다 있었는데** 탭에
  //   링크가 없어서 아무도 못 갔다. 광고그룹 On/Off 스위치(`PUT /scope/adgroup`)가 안 쓰인
  //   이유가 기능 부재가 아니라 **도달 불능**이었다(122 설계서 §2-2·§7½).
  //   탭이 8→10으로 «는다» — 최종형(탭 3개)의 역방향이지만 도달이 먼저다. 4·5단계에서
  //   두 화면이 셋팅 면·격자 행으로 흡수되면 이 두 줄이 함께 빠진다.
  { to: "/naver-ad/scope", label: "PAO 스코프" },
  { to: "/naver-ad/exclusion-list", label: "검색어 제외" },
  // 「수정 사항」 — 그날 광고에 일어난 수정 전건 + 누가 했나(두 원천 합본). 라이브 editTm을
  // 손으로 대조하던 일을 대체한다.
  // 「소재 성과」 — 소재별 ROAS를 BEP와 나란히. 캠페인 평균이 적자 소재를 가리던
  // 문제를 없앤다(D-NAO-140).
  { to: "/naver-ad/creatives", label: "소재 성과" },
  { to: "/naver-ad/modifications", label: "수정 사항" },
  { to: "/naver-ad/raw", label: "원자료" },
];

export function LayerNav() {
  return (
    <nav className="flex flex-wrap gap-1 border-b border-gray-200 mb-4">
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
