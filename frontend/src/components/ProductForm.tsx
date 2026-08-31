// ProductForm.tsx — 상품 등록/수정 모달 폼
//
// ★원가 칸은 **읽기 전용이다** (계약 D-CPP-64 §4 S1-②, 2026-08-31). 이 폼이 보내던
//   `cost_price`는 백엔드에서 무검사 `setattr`로 그대로 들어갔고 — 업로드 경로엔 걸려 있던
//   드리프트 가드가 여기엔 없었다(ref 119 §3-1) — 이력도 안 남았다. 같은 칸에 「잠긴 문」과
//   「안 잠긴 문」이 공존한 자리다. Jino가 정한 원칙(계약 §2-0)이 어느 쪽을 닫을지 정한다:
//   *"원가는 무조건 sellC의 원가 메뉴를 참고해"*.
//   ⇒ 값을 **안 보낸다**(백엔드는 보내면 400으로 거부한다 — 방어는 두 겹이다).
//     대신 지금 값을 보여 주고 **어디로 가야 하는지**를 링크로 말한다. 칸을 그냥 지우면
//     「원가가 어디 갔지」가 되고, 그건 닫는 게 아니라 숨기는 것이다.
import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { COST_MENU_PATH, COST_PRICE_REJECTION_SENTENCE } from "../lib/costPriceGate";

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
    category: string | null;
    memo: string | null;
  }) => void;
  onCancel: () => void;
  title: string;
}

export default function ProductForm({ initial, onSubmit, onCancel, title }: Props) {
  const [sku, setSku] = useState(initial?.internal_sku ?? "");
  const [name, setName] = useState(initial?.product_name ?? "");
  const [category, setCategory] = useState(initial?.category ?? "");
  const [memo, setMemo] = useState(initial?.memo ?? "");

  useEffect(() => {
    if (initial) {
      setSku(initial.internal_sku);
      setName(initial.product_name);
      setCategory(initial.category);
      setMemo(initial.memo);
    }
  }, [initial]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // ★`cost_price`를 **키 자체로** 안 싣는다. `undefined`나 기존 값을 실어 보내면 백엔드
    //   가드(`model_fields_set`)가 「보냈다」로 읽어 정상 수정까지 400이 된다.
    onSubmit({
      internal_sku: sku,
      product_name: name,
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
          <div data-testid="product-form-cost-locked">
            <label className="block text-sm font-medium text-gray-700">원가 (원)</label>
            <div className="mt-1 w-full border rounded-md px-3 py-2 text-sm bg-gray-100 text-gray-600">
              {initial ? initial.cost_price.toLocaleString() : "원가 메뉴가 정한다"}
            </div>
            {/* ★사유를 «문장으로» 말하고 길을 준다 — 회색 칸만 두면 「왜 안 되지」로 끝난다. */}
            <p className="mt-1 text-xs text-gray-500">
              {COST_PRICE_REJECTION_SENTENCE} ·{" "}
              <Link to={COST_MENU_PATH} className="text-blue-600 underline">
                원가 메뉴 열기
              </Link>
            </p>
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
