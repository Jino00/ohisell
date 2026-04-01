// MappingForm.tsx — 채널 매핑 추가 모달 폼
import { useState } from "react";
import type { Channel } from "../lib/api";

interface Props {
  channels: Channel[];
  onSubmit: (data: {
    channel_id: number;
    channel_product_id: string;
    channel_product_name: string | null;
    channel_sku: string | null;
    selling_price: string;
  }) => void;
  onCancel: () => void;
}

export default function MappingForm({ channels, onSubmit, onCancel }: Props) {
  const [channelId, setChannelId] = useState(channels[0]?.id ?? 0);
  const [pid, setPid] = useState("");
  const [pname, setPname] = useState("");
  const [sku, setSku] = useState("");
  const [price, setPrice] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit({
      channel_id: channelId,
      channel_product_id: pid,
      channel_product_name: pname || null,
      channel_sku: sku || null,
      selling_price: price || "0",
    });
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <form
        onSubmit={handleSubmit}
        className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md"
      >
        <h3 className="text-lg font-bold mb-4">채널 매핑 추가</h3>
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700">채널</label>
            <select
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={channelId}
              onChange={(e) => setChannelId(Number(e.target.value))}
            >
              {channels.map((ch) => (
                <option key={ch.id} value={ch.id}>
                  {ch.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              채널 상품번호/옵션ID
            </label>
            <input
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={pid}
              onChange={(e) => setPid(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              채널 상품명
            </label>
            <input
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={pname}
              onChange={(e) => setPname(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">채널 SKU</label>
            <input
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={sku}
              onChange={(e) => setSku(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              판매가 (원)
            </label>
            <input
              type="number"
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
            />
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-md"
          >
            취소
          </button>
          <button
            type="submit"
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            추가
          </button>
        </div>
      </form>
    </div>
  );
}
