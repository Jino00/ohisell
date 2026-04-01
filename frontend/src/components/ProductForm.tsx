// ProductForm.tsx — 상품 등록/수정 모달 폼
import { useState, useEffect } from "react";

interface Props {
  initial?: {
    internal_sku: string;
    product_name: string;
    cost_price: number;
    category: string;
    memo: string;
  };
  onSubmit: (data: {
    internal_sku: string;
    product_name: string;
    cost_price: string;
    category: string | null;
    memo: string | null;
  }) => void;
  onCancel: () => void;
  title: string;
}

export default function ProductForm({ initial, onSubmit, onCancel, title }: Props) {
  const [sku, setSku] = useState(initial?.internal_sku ?? "");
  const [name, setName] = useState(initial?.product_name ?? "");
  const [cost, setCost] = useState(initial?.cost_price?.toString() ?? "");
  const [category, setCategory] = useState(initial?.category ?? "");
  const [memo, setMemo] = useState(initial?.memo ?? "");

  useEffect(() => {
    if (initial) {
      setSku(initial.internal_sku);
      setName(initial.product_name);
      setCost(initial.cost_price.toString());
      setCategory(initial.category);
      setMemo(initial.memo);
    }
  }, [initial]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit({
      internal_sku: sku,
      product_name: name,
      cost_price: cost || "0",
      category: category || null,
      memo: memo || null,
    });
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <form
        onSubmit={handleSubmit}
        className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md"
      >
        <h3 className="text-lg font-bold mb-4">{title}</h3>
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700">사내 SKU</label>
            <input
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={sku}
              onChange={(e) => setSku(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">상품명</label>
            <input
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">원가 (원)</label>
            <input
              type="number"
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">카테고리</label>
            <input
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">메모</label>
            <textarea
              className="mt-1 w-full border rounded-md px-3 py-2 text-sm"
              value={memo}
              onChange={(e) => setMemo(e.target.value)}
              rows={2}
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
            저장
          </button>
        </div>
      </form>
    </div>
  );
}
