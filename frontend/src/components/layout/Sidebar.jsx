import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, Briefcase, Signal, BarChart3,
  Settings, Activity, TrendingUp, Shield
} from 'lucide-react';

const NAV = [
  { section: 'Trading' },
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/portfolio', icon: Briefcase, label: 'Portfolio' },
  { to: '/signals', icon: Signal, label: 'Signals' },
  { to: '/markets', icon: BarChart3, label: 'Markets' },
  { section: 'System' },
  { to: '/agent', icon: Activity, label: 'Agent' },
  { to: '/analytics', icon: TrendingUp, label: 'Analytics' },
  { to: '/risk', icon: Shield, label: 'Risk' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <h1>DALIOS</h1>
        <span className="version">v2.0</span>
      </div>
      <nav className="sidebar-nav">
        {NAV.map((item, i) =>
          item.section ? (
            <div key={i} className="sidebar-section">{item.section}</div>
          ) : (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            >
              <item.icon />
              {item.label}
            </NavLink>
          )
        )}
      </nav>
    </aside>
  );
}
