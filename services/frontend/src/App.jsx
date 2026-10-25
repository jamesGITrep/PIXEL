import React, { useState, useEffect } from 'react';
import LandingPage from './components/LandingPage';
import LiveEventArena from './components/LiveEventArena';
import StudioWorkshop from './components/StudioWorkshop';
import AdminTelemetry from './components/AdminTelemetry';

export default function App() {
  // Determine pathway from URL pathname or state
  const getInitialPathway = () => {
    const path = window.location.pathname.toLowerCase();
    if (path === '/live') return 'live';
    if (path === '/studio') return 'studio';
    if (path === '/admin') return 'admin';
    return 'landing';
  };

  const [pathway, setPathway] = useState(getInitialPathway);

  useEffect(() => {
    const handlePopState = () => {
      setPathway(getInitialPathway());
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigateTo = (newPathway) => {
    setPathway(newPathway);
    const targetUrl = newPathway === 'landing' ? '/' : `/${newPathway}`;
    window.history.pushState({}, '', targetUrl);
  };

  return (
    <div>
      <div className="ambient-glow-1" />
      <div className="ambient-glow-2" />

      {/* Global Minimalist Header */}
      <header className="app-header">
        <div className="brand-wrap" onClick={() => navigateTo('landing')}>
          <div className="brand-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <rect x="3" y="3" width="7" height="7" />
              <rect x="14" y="3" width="7" height="7" />
              <rect x="14" y="14" width="7" height="7" />
              <rect x="3" y="14" width="7" height="7" />
            </svg>
          </div>
          <div>
            <h1 className="brand-title">Collaborative Raster Platform</h1>
          </div>
          <span className="brand-tag">Bucket-Only Compute</span>
        </div>

        {/* Public Header Navigation (Zero admin/cloud metrics shown) */}
        <nav className="header-nav">
          <button
            className={`btn-nav ${pathway === 'landing' ? 'active' : ''}`}
            onClick={() => navigateTo('landing')}
          >
            Home
          </button>
          <button
            className={`btn-nav ${pathway === 'live' ? 'active' : ''}`}
            onClick={() => navigateTo('live')}
          >
            Live Event Arena
          </button>
          <button
            className={`btn-nav ${pathway === 'studio' ? 'active' : ''}`}
            onClick={() => navigateTo('studio')}
          >
            Studio Workshop
          </button>
        </nav>
      </header>

      {/* Primary Pathway Views */}
      <main>
        {pathway === 'landing' && <LandingPage onSelectPathway={navigateTo} />}
        {pathway === 'live' && <LiveEventArena />}
        {pathway === 'studio' && <StudioWorkshop />}
        {pathway === 'admin' && <AdminTelemetry />}
      </main>
    </div>
  );
}
