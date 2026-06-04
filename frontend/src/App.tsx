// App.tsx — 라우팅 설정
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Orders from "./pages/Orders";
import Products from "./pages/Products";
import InventoryPage from "./pages/InventoryPage";
import Settlements from "./pages/Settlements";
import Settings from "./pages/Settings";
import AdReport from "./pages/AdReport";
import CommandCenter from "./pages/CommandCenter";
import CoupangOps from "./pages/CoupangOps";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="command-center" element={<CommandCenter />} />
          <Route path="coupang-ops" element={<CoupangOps />} />
          <Route path="orders" element={<Orders />} />
          <Route path="products" element={<Products />} />
          <Route path="inventory" element={<InventoryPage />} />
          <Route path="settlements" element={<Settlements />} />
          <Route path="settings" element={<Settings />} />
          <Route path="ad-report" element={<AdReport />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
