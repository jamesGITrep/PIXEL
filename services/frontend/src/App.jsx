import React, { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react';
import LandingPage from './components/LandingPage';
import LiveEventArena from './components/LiveEventArena';
import StudioWorkshop from './components/StudioWorkshop';
import CommunityGallery from './components/CommunityGallery';
import AuthDialog from './components/AuthDialog';
import AccountPage from './components/AccountPage';
import { IconLogo, IconCamera, IconGrid, IconGallery } from './components/Icons';

// The admin console is unlisted: no link points to it, its code is a separate chunk that only
// loads on its own URL, and the URL itself is not stored in this bundle (it is matched by hash).
const AdminTelemetry = lazy(() => import('./components/AdminTelemetry'));
const ADMIN_PATH_HASH = 926375636;
const fnv1a = (s) => {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
};

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';

// Password-reset links arrive as /?reset=<token>; read it once and drop it from the address bar
const takeResetToken = () => {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('reset');
  if (token) window.history.replaceState({}, '', window.location.pathname);
  return token;
};

const PUBLIC_ROUTES = { '/': 'landing', '/live': 'live', '/studio': 'studio', '/gallery': 'gallery', '/community': 'gallery', '/account': 'account' };

const TITLES = {
  landing: 'PixelMosaic — Photo Mosaic Maker & Live Event Photo Wall',
  live: 'Live Event Arena — Crowd Photo Mosaic Wall | PixelMosaic',
  studio: 'Photo Mosaic Studio — Turn Your Photos Into a Mosaic | PixelMosaic',
  gallery: 'Community Photo Gallery | PixelMosaic',
  account: 'Account Settings | PixelMosaic',
  admin: 'PixelMosaic',
};

const resolvePathway = () => {
  const path = window.location.pathname.toLowerCase().replace(/\/+$/, '') || '/';
  if (PUBLIC_ROUTES[path]) return PUBLIC_ROUTES[path];
  if (fnv1a(path) === ADMIN_PATH_HASH) return 'admin';
  return null; // unknown path
};

export default function App() {
  const [pathway, setPathway] = useState(() => {
    const resolved = resolvePathway();
    // Unknown URLs (including the old /admin) land on the home page
    if (!resolved) window.history.replaceState({}, '', '/');
    return resolved || 'landing';
  });

  // Signed-in account (null = guest, undefined = still checking). The session is an HttpOnly cookie.
  const [user, setUser] = useState(undefined);
  const [resetToken] = useState(takeResetToken);
  const [authMode, setAuthMode] = useState(() => (resetToken ? 'reset' : null));

  useEffect(() => {
    fetch(`${GATEWAY_URL}/api/v1/auth/me`, { credentials: 'same-origin' })
      .then((res) => (res.ok ? res.json() : { user: null }))
      .then((data) => setUser(data.user || null))
      .catch(() => setUser(null));
  }, []);

  const [flash, setFlash] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const returnToRef = useRef(null); // page to open once the sign-in dialog succeeds

  const openAuth = useCallback((mode = 'login') => setAuthMode(mode), []);
  const closeAuth = useCallback(() => { setAuthMode(null); returnToRef.current = null; }, []);
  const handleSignedIn = useCallback(async (u) => {
    setAuthMode(null);
    // Photos uploaded as a guest in this browser session move into the account
    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/library/guest/claim`, { method: 'POST', credentials: 'same-origin' });
      const moved = res.ok ? (await res.json()).moved_photos : 0;
      if (moved > 0) setFlash(`${moved} photo${moved === 1 ? '' : 's'} you uploaded before signing in ${moved === 1 ? 'is' : 'are'} now saved in your account.`);
    } catch { /* nothing to move */ }
    setUser(u);
    if (returnToRef.current) {
      const target = returnToRef.current;
      returnToRef.current = null;
      setPathway(target);
      window.history.pushState({}, '', `/${target}`);
    }
  }, []);
  const signOut = async () => {
    setMenuOpen(false);
    try {
      await fetch(`${GATEWAY_URL}/api/v1/auth/logout`, { method: 'POST', credentials: 'same-origin' });
    } finally {
      if (pathway === 'account') {
        setPathway('landing');
        window.history.replaceState({}, '', '/');
      }
      setUser(null);
    }
  };

  // Avatar dropdown: closes on outside click and Escape
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDown = (e) => { if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [menuOpen]);

  useEffect(() => {
    const handlePopState = () => {
      setPathway(resolvePathway() || 'landing');
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Per-page titles; the admin console is additionally marked noindex for any crawler that
  // somehow reaches it (the server also sends X-Robots-Tag for that URL).
  useEffect(() => {
    document.title = TITLES[pathway] || TITLES.landing;
    let robots = document.querySelector('meta[name="robots"]');
    if (!robots) {
      robots = document.createElement('meta');
      robots.name = 'robots';
      document.head.appendChild(robots);
    }
    robots.content = pathway === 'admin' || pathway === 'account' ? 'noindex, nofollow, noarchive' : 'index, follow';
  }, [pathway]);

  // /account is for signed-in users only: guests go home with the sign-in dialog open, and land
  // back on /account after signing in.
  useEffect(() => {
    if (pathway === 'account' && user === null) {
      returnToRef.current = 'account';
      setPathway('landing');
      window.history.replaceState({}, '', '/');
      setAuthMode('login');
    }
  }, [pathway, user]);

  const handleAccountDeleted = (photoCount) => {
    setUser(null);
    setPathway('landing');
    window.history.replaceState({}, '', '/');
    setFlash(`Your account and ${photoCount} personal photo${photoCount === 1 ? '' : 's'} were permanently deleted. You have been signed out.`);
  };

  const navigateTo = (newPathway) => {
    setMenuOpen(false);
    setPathway(newPathway);
    const targetUrl = newPathway === 'landing' ? '/' : `/${newPathway}`;
    window.history.pushState({}, '', targetUrl);
  };

  return (
    <div>
      <div className="app-backdrop-grid" />
      <div className="ambient-light-top" />

      {/* Global Executive Header */}
      <header className="app-header">
        <div className="brand-wrap" onClick={() => navigateTo('landing')}>
          <div className="brand-icon-box">
            <IconLogo size={18} />
          </div>
          <div className="brand-text-wrap">
            <span className="brand-title">PixelMosaic</span>
            <span className="brand-pill">Raster Engine</span>
          </div>
        </div>

        {/* Public Header Navigation */}
        <nav className="header-nav">
          <button
            className={`btn-nav-link ${pathway === 'landing' ? 'active' : ''}`}
            onClick={() => navigateTo('landing')}
          >
            Overview
          </button>
          <button
            className={`btn-nav-link ${pathway === 'live' ? 'active' : ''}`}
            onClick={() => navigateTo('live')}
          >
            <IconCamera size={15} />
            Live Event Arena
          </button>
          <button
            className={`btn-nav-link ${pathway === 'studio' ? 'active' : ''}`}
            onClick={() => navigateTo('studio')}
          >
            <IconGrid size={15} />
            Studio Workshop
          </button>
          <button
            className={`btn-nav-link ${pathway === 'gallery' ? 'active' : ''}`}
            onClick={() => navigateTo('gallery')}
          >
            <IconGallery size={15} />
            Community Gallery
          </button>
          <div className="header-account">
            {user ? (
              <div className="avatar-menu" ref={menuRef}>
                <button
                  className={`avatar-button ${pathway === 'account' ? 'active' : ''}`}
                  onClick={() => setMenuOpen((o) => !o)}
                  aria-haspopup="menu"
                  aria-expanded={menuOpen}
                  title={user.email}
                >
                  <span className="avatar-circle" aria-hidden="true">{user.username.charAt(0).toUpperCase()}</span>
                  <span className="header-username">{user.username}</span>
                  <span className="avatar-caret" aria-hidden="true">▾</span>
                </button>
                {menuOpen && (
                  <div className="avatar-dropdown" role="menu">
                    <div className="avatar-dropdown-head">
                      <strong>{user.username}</strong>
                      <span>{user.email}</span>
                    </div>
                    <button role="menuitem" onClick={() => navigateTo('account')}>Account Settings</button>
                    <button role="menuitem" onClick={signOut}>Sign out</button>
                  </div>
                )}
              </div>
            ) : (
              <button className="btn-nav-link" onClick={() => openAuth('login')} disabled={user === undefined}>
                Sign in
              </button>
            )}
          </div>
        </nav>
      </header>

      {/* Primary Pathway Views */}
      <main>
        {flash && (
          <div className="app-flash" role="status">
            <span>{flash}</span>
            <button onClick={() => setFlash(null)} aria-label="Dismiss">&times;</button>
          </div>
        )}
        {pathway === 'landing' && <LandingPage onSelectPathway={navigateTo} />}
        {pathway === 'live' && <LiveEventArena />}
        {pathway === 'studio' && <StudioWorkshop user={user} onRequestSignIn={openAuth} onNavigateToGallery={() => navigateTo('gallery')} />}
        {pathway === 'gallery' && <CommunityGallery onNavigateToStudio={() => navigateTo('studio')} />}
        {pathway === 'account' && user && (
          <AccountPage user={user} onAccountDeleted={handleAccountDeleted} onNavigateToStudio={() => navigateTo('studio')} />
        )}
        {pathway === 'admin' && (
          <Suspense fallback={null}>
            <AdminTelemetry user={user} onRequestSignIn={openAuth} />
          </Suspense>
        )}
      </main>

      {authMode && (
        <AuthDialog mode={authMode} resetToken={resetToken} onClose={closeAuth} onSignedIn={handleSignedIn} />
      )}
    </div>
  );
}
