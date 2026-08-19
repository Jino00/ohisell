// partialSync.ts — 「동기화는 성공인데 덜 들어옴」 판별 (D-NAO-204)
//
// ★왜 페이지에서 분리했나: 이 분기는 테스트가 없으면 조용히 죽어도 아무도 모른다. 그게 바로
//   이 코드가 고치고 있는 결함의 모양이다(백엔드는 세는데 화면이 안 읽음). 페이지 컴포넌트를
//   통째로 import하면 라우터·차트 의존성이 딸려와 테스트가 무거워지고 잘 깨진다.
// ★표식(`[부분수집]`)의 정본은 백엔드 `sync_service.PARTIAL_SYNC_MARKER`다. 여기 값은 그 사본이고,
//   갈라지면 화면이 조용해진다 — 백엔드를 바꾸면 여기도 바꿔야 한다.
import type { SyncStatus } from "../lib/api";

export const PARTIAL_SYNC_MARKER = "[부분수집]";

/** `status='success'`인데 `error_message`가 부분수집인 채널만 고른다.
 *  `error`는 이미 빨강으로 보이므로 제외 — 여기서 잡을 것은 **초록으로 보이는데 덜 들어온 것**이다. */
export function partialSyncChannels(statuses: SyncStatus[]): SyncStatus[] {
  return statuses.filter(
    (s) => s.status === "success" && (s.error_message ?? "").startsWith(PARTIAL_SYNC_MARKER),
  );
}
