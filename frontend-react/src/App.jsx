import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import MarketEvents from './pages/MarketEvents';
import Analytics from './pages/Analytics';
import Journal from './pages/Journal';
import Scanner from './pages/Scanner';
import StockDetail from './pages/StockDetail';
import Dashboard from './pages/Dashboard';
import PortfolioSimulation from './pages/PortfolioSimulation';
import MentorCalls from './pages/MentorCalls';
import Settings from './pages/Settings';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/scanner" element={<Scanner />} />
          <Route path="/stock-detail" element={<StockDetail />} />
          <Route path="/market-events" element={<MarketEvents />} />
          <Route path="/portfolio-simulation" element={<PortfolioSimulation />} />
          <Route path="/mentor-calls" element={<MentorCalls />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
