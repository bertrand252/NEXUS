import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import Login from './pages/Login';
import MarketEvents from './pages/MarketEvents';
import Analytics from './pages/Analytics';
import Journal from './pages/Journal';
import Scanner from './pages/Scanner';
import StockDetail from './pages/StockDetail';
import Dashboard from './pages/Dashboard';
import PortfolioSimulation from './pages/PortfolioSimulation';
import Settings from './pages/Settings';
import { useAuth } from './hooks/useAuth';

function RequireAuth({ children }) {
  const { session, loading } = useAuth();
  if (loading) return null;
  if (!session) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/scanner" element={<Scanner />} />
          <Route path="/stock-detail" element={<StockDetail />} />
          <Route path="/market-events" element={<MarketEvents />} />
          <Route path="/portfolio-simulation" element={<PortfolioSimulation />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
