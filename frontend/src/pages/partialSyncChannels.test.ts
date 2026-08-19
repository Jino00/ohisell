// partialSyncChannels.test.ts — 주문 화면의 «성공인데 덜 들어옴» 판별 (D-NAO-204)
// ★status='error'는 이미 빨강으로 보인다. 여기서 잡아야 하는 것은 **초록으로 보이는데
//   덜 들어온 것**이다 — 그 구분이 이 함수의 전부다.
import { describe, it, expect } from "vitest";
import { partialSyncChannels } from "./partialSync";
import type { SyncStatus } from "../lib/api";

const st = (over: Partial<SyncStatus> = {}): SyncStatus => ({
  channel_id: 6,
  channel_name: "네이버 스마트스토어",
  last_sync: "2026-08-19T10:25:12+09:00",
  status: "success",
  records_synced: 336,
  ...over,
});

describe("partialSyncChannels", () => {
  it("success + [부분수집] 이면 잡는다", () => {
    const out = partialSyncChannels([
      st({ error_message: "[부분수집] 변경상태 스윕 미완주 1일: 2026-08-18" }),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].channel_id).toBe(6);
  });

  it("정상 success는 안 잡는다", () => {
    expect(partialSyncChannels([st(), st({ error_message: null })])).toHaveLength(0);
  });

  it("error 상태는 안 잡는다 — 이미 빨강으로 보인다", () => {
    expect(
      partialSyncChannels([st({ status: "error", error_message: "[부분수집] …" })]),
    ).toHaveLength(0);
  });

  it("다른 error_message는 안 잡는다 (표식이 계약이다)", () => {
    expect(
      partialSyncChannels([st({ error_message: "이미 동기화가 진행 중입니다" })]),
    ).toHaveLength(0);
  });

  it("여러 채널 중 해당하는 것만 고른다", () => {
    const out = partialSyncChannels([
      st(),
      st({ channel_id: 7, channel_name: "자사몰", error_message: "[부분수집] 상세조회 실패 2청크" }),
      st({ channel_id: 1, status: "error", error_message: "네트워크 오류" }),
    ]);
    expect(out.map((s) => s.channel_id)).toEqual([7]);
  });
});
