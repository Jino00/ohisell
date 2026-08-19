// PipelineHealthBanner.tsx — 파이프라인 경고 배너의 «표시» 절반 (D-NAO-205).
//
// ★왜 파일을 갈랐나 (적대 리뷰 2026-08-19): 판정(`buildPipelineHealthBanner`)에는 테스트가
//   촘촘한데 **화면 절반은 무방비**였다 — 토글 조건을 `>1`에서 `>0`으로 바꾸거나, 접힘에서
//   `items[0]` 대신 마지막 항목을 그리거나, 펼침 `<ul>`을 통째로 지워도 순수 함수 테스트
//   47건이 전부 통과했다. Layout 전체는 라우터 의존이라 렌더 테스트가 무거우므로, 표시만
//   떼어 **DOM으로 검증 가능한 모양**으로 만든다(같은 사유의 선례: `periodRangeBarClick.test.tsx`).
//   짝 파일은 `pipelineHealthBanner.dom.test.tsx`.
import { useState } from "react";
import { Link } from "react-router-dom";

export function PipelineHealthBanner({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false);
  // ★항목이 1건 이하로 줄면 토글 버튼이 사라지는데 `open`은 true로 남아 **접을 방법이 없는
  //   펼침**이 된다(적대 리뷰 P2). 폴링으로 경고가 해소되는 흐름이라 실제로 일어난다.
  //   렌더 중 파생값으로 눌러 «기본 접힘» 계약이 새로고침 없이도 유지되게 한다.
  const expanded = open && items.length > 1;

  return (
    <div className="bg-amber-600 text-white px-4 py-2 text-sm">
      {/* ★접기/펼치기(Jino 확정): 종전엔 한 줄 `truncate`뿐이라 경고가 많으면 뒤 항목이
          **화면에서 통째로 사라졌다** — 2026-08-19 실측에서 11건 중 매출에 닿는 「주문이 덜
          수집됨」이 잘려 안 보였다. 호버 `title`에는 있었지만 «호버해야 보이는 경고»는 배너가
          아니다. 기본은 접힘이라 평소 화면 높이는 그대로다. */}
      <div className="flex items-center gap-3">
        <span className="font-semibold shrink-0">
          ⚠️ 파이프라인 경고{expanded && ` (${items.length}건)`}
        </span>
        {!expanded && (
          <span className="text-amber-100 min-w-0 truncate" title={items.join("\n")}>
            {items[0]}
          </span>
        )}
        {items.length > 1 && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={expanded}
            aria-controls="pipeline-health-items"
            className="shrink-0 underline underline-offset-2 hover:text-amber-100"
          >
            {expanded ? "접기 ▴" : `외 ${items.length - 1}건 ▾`}
          </button>
        )}
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
      {expanded && (
        // ★펼쳤을 때는 자르지 않는다 — 자르면 펼친 의미가 없다. 항목이 많으면 스크롤한다.
        <ul
          id="pipeline-health-items"
          className="mt-1 max-h-64 overflow-y-auto list-disc list-inside text-amber-100 space-y-0.5"
        >
          {items.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
