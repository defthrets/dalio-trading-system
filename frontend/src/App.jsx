import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/layout/Layout';
import Dashboard from './pages/Dashboard';
import Portfolio from './pages/Portfolio';
import Signals from './pages/Signals';
import Markets from './pages/Markets';
import Agent from './pages/Agent';
import Analytics from './pages/Analytics';
import Risk from './pages/Risk';
import Settings from './pages/Settings';
import './styles/theme.css';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="portfolio" element={<Portfolio />} />
          <Route path="signals" element={<Signals />} />
          <Route path="markets" element={<Markets />} />
          <Route path="agent" element={<Agent />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="risk" element={<Risk />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
