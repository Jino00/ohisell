// Products.tsx — 상품 원가표 관리 페이지
import { useState, useEffect, useCallback, useRef, Fragment } from "react";
import {
  fetchApi,
  uploadFile,
  downloadUrl,
  type Product,
  type Channel,
} from "../lib/api";
import { buildCostSheetUploadText } from "./uploadResultText";
import ProductForm from "../components/ProductForm";
import MappingForm from "../components/MappingForm";

export default function Products() {
  const [products, setProducts] = useState<Product[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [editProduct, setEditProduct] = useState<Product | null>(null);
  const [mappingTarget, setMappingTarget] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [uploadMsg, setUploadMsg] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const [p, c] = await Promise.all([
      fetchApi<Product[]>("/api/products"),
      fetchApi<Channel[]>("/api/channels"),
    ]);
    setProducts(p);
    setChannels(c);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(data: Record<string, unknown>) {
    await fetchApi("/api/products", {
      method: "POST",
      body: JSON.stringify(data),
    });
    setShowCreate(false);
    load();
  }

  async function handleUpdate(data: Record<string, unknown>) {
    if (!editProduct) return;
    await fetchApi(`/api/products/${editProduct.id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
    setEditProduct(null);
    load();
  }

  async function handleDelete(id: number) {
    if (!confirm("이 상품을 삭제하시겠습니까?")) return;
    await fetchApi(`/api/products/${id}`, { method: "DELETE" });
    load();
  }

  async function handleAddMapping(data: Record<string, unknown>) {
    if (mappingTarget === null) return;
    // 응답의 orders_linked = 이 옵션ID의 **과거 미연결 주문 중 방금 소급 연결된 건수**
    // (2026-08-04). 조용히 지나가면 "이었는데 손익이 안 바뀐다"로 오해된다.
    const created = await fetchApi<{ orders_linked?: number | null }>(
      `/api/products/${mappingTarget}/mappings`,
      { method: "POST", body: JSON.stringify(data) },
    );
    setMappingTarget(null);
    if (created.orders_linked) {
      alert(`과거 주문 ${created.orders_linked}건이 이 상품에 소급 연결됐습니다(원가·손익 반영).`);
    }
    load();
  }

  async function handleDeleteMapping(productId: number, mappingId: number) {
    await fetchApi(`/api/products/${productId}/mappings/${mappingId}`, {
      method: "DELETE",
    });
    load();
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const result = await uploadFile("/api/products/upload", file);
      // ★문구는 uploadResultText.ts(순수)가 조립한다 — 원가 거부·미검사가 표시에서
      //   빠지는 것을 테스트가 잡게 하려는 것이다(적대 리뷰 2R P2-N1).
      setUploadMsg(buildCostSheetUploadText(result));
      load();
    } catch (err) {
      setUploadMsg(`업로드 실패: ${err}`);
    }
    if (fileRef.current) fileRef.current.value = "";
  }

  const fmt = (n: number) =>
    new Intl.NumberFormat("ko-KR").format(n);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-2xl font-bold text-gray-900">상품 원가표</h2>
        <div className="flex gap-2">
          <a
            href={downloadUrl("/api/products/mapping-template")}
            className="px-3 py-2 text-sm bg-purple-600 text-white rounded-md hover:bg-purple-700"
          >
            매핑 템플릿
          </a>
          <a
            href={downloadUrl("/api/products/download")}
            className="px-3 py-2 text-sm bg-green-600 text-white rounded-md hover:bg-green-700"
          >
            엑셀 다운로드
          </a>
          <label className="px-3 py-2 text-sm bg-gray-500 text-white rounded-md hover:bg-gray-600 cursor-pointer">
            SKU 업로드
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,.xls"
              className="hidden"
              onChange={handleUpload}
            />
          </label>
          <button
            onClick={() => setShowCreate(true)}
            className="px-3 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700"
          >
            + 상품 추가
          </button>
        </div>
      </div>

      {uploadMsg && (
        <div className="mb-3 p-3 bg-blue-50 text-blue-800 rounded-md text-sm">
          {uploadMsg}
          <button
            onClick={() => setUploadMsg("")}
            className="ml-2 text-blue-600 underline"
          >
            닫기
          </button>
        </div>
      )}

      {/* 상품 테이블 */}
      <div className="bg-white rounded-lg border">
        <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b bg-gray-50">
              <th className="text-left px-4 py-3 font-medium text-gray-600">SKU</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">상품명</th>
              <th className="text-right px-4 py-3 font-medium text-gray-600">원가</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600">카테고리</th>
              <th className="text-center px-4 py-3 font-medium text-gray-600">매핑</th>
              <th className="text-center px-4 py-3 font-medium text-gray-600">액션</th>
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <Fragment key={p.id}>
                <tr
                  className="border-b hover:bg-gray-50 cursor-pointer"
                  onClick={() =>
                    setExpandedId(expandedId === p.id ? null : p.id)
                  }
                >
                  <td className="px-4 py-3 font-mono text-gray-700">
                    {p.internal_sku}
                  </td>
                  <td className="px-4 py-3">{p.product_name}</td>
                  <td className="px-4 py-3 text-right">
                    {fmt(p.cost_price)}원
                  </td>
                  <td className="px-4 py-3 text-gray-500">{p.category}</td>
                  <td className="px-4 py-3 text-center">
                    <span className="bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full text-xs">
                      {p.mappings.length}개
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditProduct(p);
                      }}
                      className="text-blue-600 hover:underline mr-2"
                    >
                      수정
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(p.id);
                      }}
                      className="text-red-600 hover:underline"
                    >
                      삭제
                    </button>
                  </td>
                </tr>
                {expandedId === p.id && (
                  <tr key={`${p.id}-detail`}>
                    <td colSpan={6} className="px-4 py-3 bg-gray-50">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-medium text-gray-700">
                          채널 매핑
                        </span>
                        <button
                          onClick={() => setMappingTarget(p.id)}
                          className="text-xs bg-blue-500 text-white px-2 py-1 rounded hover:bg-blue-600"
                        >
                          + 매핑 추가
                        </button>
                      </div>
                      {p.mappings.length === 0 ? (
                        <p className="text-gray-400 text-sm">매핑 없음</p>
                      ) : (
                        <div className="overflow-x-auto">
                        <table className="min-w-full text-xs">
                          <thead>
                            <tr className="text-gray-500">
                              <th className="text-left py-1">채널</th>
                              <th className="text-left py-1">상품번호</th>
                              <th className="text-left py-1">상품명</th>
                              <th className="text-left py-1">SKU</th>
                              <th className="text-right py-1">판매가</th>
                              <th className="text-center py-1"></th>
                            </tr>
                          </thead>
                          <tbody>
                            {p.mappings.map((m) => (
                              <tr key={m.id} className="border-t border-gray-200">
                                <td className="py-1">{m.channel_name}</td>
                                <td className="py-1 font-mono">{m.channel_product_id}</td>
                                <td className="py-1">{m.channel_product_name}</td>
                                <td className="py-1 font-mono">{m.channel_sku}</td>
                                <td className="py-1 text-right">{fmt(m.selling_price)}원</td>
                                <td className="py-1 text-center">
                                  <button
                                    onClick={() => handleDeleteMapping(p.id, m.id)}
                                    className="text-red-500 hover:underline"
                                  >
                                    삭제
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {products.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  등록된 상품이 없습니다. 상품을 추가하거나 엑셀을 업로드하세요.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </div>

      {/* 모달 */}
      {showCreate && (
        <ProductForm
          title="상품 추가"
          onSubmit={handleCreate}
          onCancel={() => setShowCreate(false)}
        />
      )}
      {editProduct && (
        <ProductForm
          title="상품 수정"
          initial={{
            internal_sku: editProduct.internal_sku,
            product_name: editProduct.product_name,
            cost_price: editProduct.cost_price,
            category: editProduct.category ?? "",
            memo: editProduct.memo ?? "",
          }}
          onSubmit={handleUpdate}
          onCancel={() => setEditProduct(null)}
        />
      )}
      {mappingTarget !== null && (
        <MappingForm
          channels={channels}
          onSubmit={handleAddMapping}
          onCancel={() => setMappingTarget(null)}
        />
      )}
    </div>
  );
}
