// App.tsx — 라우팅 설정
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
// KPI 카드 4칸의 근거(계산) 페이지 — 계약 CONTRACT_kpi_evidence_page.md.
import KpiEvidence from "./pages/KpiEvidence";
import Orders from "./pages/Orders";
import Products from "./pages/Products";
import InventoryPage from "./pages/InventoryPage";
import ImportCostPage from "./pages/ImportCostPage";
import CostPage from "./pages/CostPage";
import Settlements from "./pages/Settlements";
import Settings from "./pages/Settings";
import AdReport from "./pages/AdReport";
import CommandCenter from "./pages/CommandCenter";
import CoupangOps from "./pages/CoupangOps";
import CoupangAdChanges from "./pages/CoupangAdChanges";
import RocketRecon from "./pages/RocketRecon";
import Rocket1PRevenue from "./pages/Rocket1PRevenue";
import Rocket1PFunnel from "./pages/Rocket1PFunnel";
import Rocket1PPnlAudit from "./pages/Rocket1PPnlAudit";
// RG(로켓그로스 2P) 전용 화면 — 계약 CONTRACT_2p_own_screens(D-CPP-54) §1-A.
// 1P 전용 4화면 바로 뒤에 둔다: 둘 다 «쿠팡 아래 판매방식»이라 같은 층이다(Layout.tsx:236-240).
import RocketGrowthPnl from "./pages/RocketGrowthPnl";
import RocketGrowthSettlement from "./pages/RocketGrowthSettlement";
import NaverOps from "./pages/NaverOps";
import ProductConnectionMap from "./pages/ProductConnectionMap";
import NaverAdReport from "./pages/NaverAdReport";
import NaverAdCommandCenter from "./pages/NaverAdCommandCenter";
import NaverAdScope from "./pages/NaverAdScope";
import NaverAdDiagnosisBoard from "./pages/NaverAdDiagnosisBoard";
import NaverAdOptimizationConsole from "./pages/NaverAdOptimizationConsole";
import NaverAdRawExplorer from "./pages/NaverAdRawExplorer";
import NaverAdPerformance from "./pages/NaverAdPerformance";
import NaverAdCreatives from "./pages/NaverAdCreatives";
import NaverAdModifications from "./pages/NaverAdModifications";
import NaverAdExclusionList from "./pages/NaverAdExclusionList";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="kpi-evidence" element={<KpiEvidence />} />
          <Route path="command-center" element={<CommandCenter />} />
          <Route path="coupang-ops" element={<CoupangOps />} />
          <Route path="coupang-ad-changes" element={<CoupangAdChanges />} />
          <Route path="rocket-recon" element={<RocketRecon />} />
          <Route path="rocket-1p-revenue" element={<Rocket1PRevenue />} />
          <Route path="rocket-1p-funnel" element={<Rocket1PFunnel />} />
          <Route path="rocket-1p/pnl-audit" element={<Rocket1PPnlAudit />} />
          <Route path="rocket-growth" element={<RocketGrowthPnl />} />
          <Route path="rocket-growth/settlement" element={<RocketGrowthSettlement />} />
          <Route path="naver-ops" element={<NaverOps />} />
          <Route path="orders" element={<Orders />} />
          <Route path="products" element={<Products />} />
          <Route path="product-connection-map" element={<ProductConnectionMap />} />
          <Route path="inventory" element={<InventoryPage />} />
          <Route path="import-cost" element={<ImportCostPage />} />
          {/* 원가 메뉴 — D-CPP-53 / 계약 A′ */}
          <Route path="cost" element={<CostPage />} />
          <Route path="settlements" element={<Settlements />} />
          <Route path="settings" element={<Settings />} />
          <Route path="ad-report" element={<AdReport />} />
          <Route path="naver-ad" element={<NaverAdCommandCenter />} />
          {/* PAO 스코프 — 어떤 캠페인·광고그룹을 엔진에 맡길지 + 그 성과 (D-NAO-244) */}
          <Route path="naver-ad/scope" element={<NaverAdScope />} />
          <Route path="naver-ad/performance" element={<NaverAdPerformance />} />
          <Route path="naver-ad/report" element={<NaverAdReport />} />
          <Route path="naver-ad/diagnosis" element={<NaverAdDiagnosisBoard />} />
          <Route path="naver-ad/console" element={<NaverAdOptimizationConsole />} />
          <Route path="naver-ad/creatives" element={<NaverAdCreatives />} />
          <Route path="naver-ad/modifications" element={<NaverAdModifications />} />
          <Route path="naver-ad/exclusion-list" element={<NaverAdExclusionList />} />
          <Route path="naver-ad/raw" element={<NaverAdRawExplorer />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
