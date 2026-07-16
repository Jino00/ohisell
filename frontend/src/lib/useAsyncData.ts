// useAsyncData.ts — D-NAO-47. 조회 3상태(로딩/실패/데이터)를 구조적으로 가른다.
//
// ★왜 훅으로 만드는가(codex[P2] R3·R4): 각 패널이 손으로
//     .catch(() => setData({ total: 0, rows: [] }))
// 를 쓰고 있었다. 그러면 **조회 실패가 "데이터 없음"으로 렌더된다** — 화면이
// "최근 30일 우리가 집행한 변경이 없습니다"라고 **단언**하는데, 실은 모르는 것이다.
// 커맨드 센터에서 이건 단순 버그가 아니라 원칙 위반이다: D-47-h는 "0이면 0이라고 말하라"이지
// "모르면 0이라고 말하라"가 아니다. 하필 이유까지 붙어 정직해 보이는 형태라 더 나쁘다.
// 실제로 6곳에서 같은 실수가 반복됐고, 한 곳을 고쳐도 다음 패널에서 재발했다 → 개별 수정이
// 아니라 **기본값을 바꾼다**. 이 훅을 쓰면 실패를 "빈 데이터"로 위장할 방법이 없다.
//
// 사용:
//   const { data, error } = useAsyncData(() => fetchX(), [dep]);
//   if (error) return <EmptyState reason={`불러오지 못했습니다: ${error}`} />;
//   if (data === null) return <Loading />;
//   if (data.rows.length === 0) return <EmptyState reason="…진짜 비어있는 이유…" />;
import { useEffect, useState } from "react";

export function useAsyncData<T>(
  fn: () => Promise<T>,
  deps: unknown[],
): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    fn()
      .then((d) => { if (alive) setData(d); })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error };
}
