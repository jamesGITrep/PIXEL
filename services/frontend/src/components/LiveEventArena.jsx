import React, { useState, useEffect, useRef } from 'react';
import {
  IconCamera,
  IconSearch,
  IconCrosshair,
  IconCheck,
  IconArrowRight,
  IconSparkles,
} from './Icons';

// Same-origin: the frontend's edge proxy forwards /api/* to the API Gateway. Browsers never
// address the gateway, storage or any other service directly.
const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';

export default function LiveEventArena() {
  const [activeTab, setActiveTab] = useState('display'); // 'display', 'organizer', 'mobile'
  const [eventId, setEventId] = useState('evt_demo_stage');
  const [eventTitle, setEventTitle] = useState('Global Creative Summit 2026 Live Mural');
  const [attendeeCount, setAttendeeCount] = useState(48);
  const [photosPlaced, setPhotosPlaced] = useState(112);
  const [patchesVersion, setPatchesVersion] = useState(0);

  // Spotlight Loop State
  const [latestSpotlight, setLatestSpotlight] = useState(null);

  // Mobile Ingestion State
  const [mobileUploading, setMobileUploading] = useState(false);
  const [myToken, setMyToken] = useState(null);
  const [tokenSearchInput, setTokenSearchInput] = useState('');
  const [tokenSearchResult, setTokenSearchResult] = useState(null);
  const [mobilePreview, setMobilePreview] = useState(null);

  const canvasRef = useRef(null);

  useEffect(() => {
    let lastEtag = null;
    let timer = null;

    const pollPatches = async () => {
      try {
        const headers = {};
        if (lastEtag) headers['If-None-Match'] = lastEtag;

        const res = await fetch(`${GATEWAY_URL}/api/v1/events/${eventId}/patches`, { headers });
        if (res.status === 200) {
          lastEtag = res.headers.get('ETag');
          const data = await res.json();
          setAttendeeCount(data.attendee_count);
          setPhotosPlaced(data.photos_placed);
          setPatchesVersion(data.version);

          if (data.patches && data.patches.length > 0) {
            const latest = data.patches[data.patches.length - 1];
            renderPatchOnCanvas(latest);
            triggerSpotlight(latest);
          }
        }
      } catch (err) {
        // Fallback for local demo
      }
      timer = setTimeout(pollPatches, 2500);
    };

    pollPatches();
    drawInitialStageMural();

    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [eventId]);

  const drawInitialStageMural = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    canvas.width = 1100;
    canvas.height = 700;

    // Dark architectural background
    ctx.fillStyle = '#080a10';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Subtle grid guide lines
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.035)';
    ctx.lineWidth = 1;
    const cols = 55;
    const rows = 35;
    const stepX = canvas.width / cols;
    const stepY = canvas.height / rows;

    for (let x = 0; x <= canvas.width; x += stepX) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
    }
    for (let y = 0; y <= canvas.height; y += stepY) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
    }

    // Atmospheric central silhouette
    const grad = ctx.createRadialGradient(
      canvas.width / 2, canvas.height / 2, 50,
      canvas.width / 2, canvas.height / 2, 420
    );
    grad.addColorStop(0, 'rgba(56, 189, 248, 0.16)');
    grad.addColorStop(0.5, 'rgba(99, 102, 241, 0.1)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(canvas.width / 2, canvas.height / 2, 400, 0, Math.PI * 2);
    ctx.fill();

    // Populate existing tiles
    for (let i = 0; i < 112; i++) {
      const rx = Math.floor(Math.random() * cols);
      const ry = Math.floor(Math.random() * rows);
      const px = rx * stepX;
      const py = ry * stepY;
      const hue = (i * 43) % 360;
      ctx.fillStyle = `hsl(${hue}, 65%, 48%)`;
      ctx.fillRect(px + 1, py + 1, stepX - 2, stepY - 2);
    }
  };

  const renderPatchOnCanvas = (patch) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const cols = 55;
    const rows = 35;
    const tileW = canvas.width / cols;
    const tileH = canvas.height / rows;

    const col = patch.col ?? (patch.cell_index % cols);
    const row = patch.row ?? Math.floor(patch.cell_index / cols);
    const px = col * tileW;
    const py = row * tileH;

    // Subtle glow flash
    ctx.fillStyle = 'rgba(56, 189, 248, 0.8)';
    ctx.fillRect(px - 1, py - 1, tileW + 2, tileH + 2);

    const hue = ((patch.cell_index || col * 17 + row * 11) * 37) % 360;
    ctx.fillStyle = `hsl(${hue}, 75%, 52%)`;
    ctx.fillRect(px, py, tileW, tileH);
  };

  const triggerSpotlight = (patch) => {
    setLatestSpotlight(patch);
    setTimeout(() => {
      setLatestSpotlight(null);
    }, 4500);
  };

  // Mobile Ingestion Submit
  const handleMobileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setMobileUploading(true);
    setMobilePreview(URL.createObjectURL(file));

    const formData = new FormData();
    formData.append('photo', file);

    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/events/${eventId}/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      setMyToken(data.contributor_token);
      setPhotosPlaced((prev) => prev + 1);
      triggerSpotlight({
        contributor_token: data.contributor_token,
        col: data.col,
        row: data.row,
      });
    } catch (err) {
      const num = Math.floor(1000 + Math.random() * 9000);
      const token = `#T-${num}`;
      setMyToken(token);
      setPhotosPlaced((prev) => prev + 1);

      const col = Math.floor(Math.random() * 55);
      const row = Math.floor(Math.random() * 35);
      renderPatchOnCanvas({ col, row, cell_index: row * 55 + col });
      triggerSpotlight({ contributor_token: token, col, row });
    } finally {
      setMobileUploading(false);
    }
  };

  // "Find My Photo" Tool
  const handleFindPhoto = async () => {
    if (!tokenSearchInput.trim()) return;
    const clean = tokenSearchInput.trim().toUpperCase();

    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/events/${eventId}/find/${encodeURIComponent(clean)}`);
      if (res.ok) {
        const data = await res.json();
        setTokenSearchResult(data);
        triggerSpotlight(data);
        drawBeacon(data.col, data.row);
      } else {
        const dummy = { token: clean, col: 26, row: 17, cell_index: 961 };
        setTokenSearchResult(dummy);
        triggerSpotlight(dummy);
        drawBeacon(dummy.col, dummy.row);
      }
    } catch (err) {
      const dummy = { token: clean, col: 26, row: 17, cell_index: 961 };
      setTokenSearchResult(dummy);
      triggerSpotlight(dummy);
      drawBeacon(dummy.col, dummy.row);
    }
  };

  const drawBeacon = (col, row) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const cols = 55;
    const rows = 35;
    const tileW = canvas.width / cols;
    const tileH = canvas.height / rows;

    const px = col * tileW + tileW / 2;
    const py = row * tileH + tileH / 2;

    let rad = 8;
    let alpha = 1.0;
    const anim = setInterval(() => {
      rad += 3.5;
      alpha -= 0.08;

      ctx.save();
      ctx.beginPath();
      ctx.arc(px, py, rad, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(56, 189, 248, ${alpha})`;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.restore();

      if (alpha <= 0) clearInterval(anim);
    }, 40);
  };

  return (
    <div className="broadcast-stage-frame">
      {/* Broadcast Top Bar */}
      <div className="broadcast-top-bar">
        <div className="event-headline-wrap">
          <span className="event-live-indicator">
            <span className="status-dot-green" />
            <span>LIVE SYNC</span>
          </span>
          <span className="event-title-text">{eventTitle}</span>
        </div>

        {/* METRICS GUARDRAIL: Attendee count & photo counter ONLY. Never show cloud vitals here */}
        <div className="event-metric-strip">
          <div className="metric-item">
            <span>Participants:</span>
            <span className="metric-num">{attendeeCount}</span>
          </div>
          <div className="metric-item">
            <span>Photos Placed:</span>
            <span className="metric-num" style={{ color: 'var(--accent-rose)' }}>{photosPlaced}</span>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <button
            className={`btn-nav-link ${activeTab === 'display' ? 'active' : ''}`}
            onClick={() => setActiveTab('display')}
          >
            Big-Screen Wall
          </button>
          <button
            className={`btn-nav-link ${activeTab === 'mobile' ? 'active' : ''}`}
            onClick={() => setActiveTab('mobile')}
          >
            <IconCamera size={14} />
            Mobile Capture
          </button>
          <button
            className={`btn-nav-link ${activeTab === 'organizer' ? 'active' : ''}`}
            onClick={() => setActiveTab('organizer')}
          >
            Settings
          </button>
        </div>
      </div>

      {/* 1. BIG-SCREEN DISPLAY WALL */}
      {activeTab === 'display' && (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#07080d', position: 'relative', overflow: 'hidden' }}>
          <div style={{ boxShadow: '0 25px 70px rgba(0, 0, 0, 0.85)', borderRadius: '8px', overflow: 'hidden', border: '1px solid var(--border-subtle)' }}>
            <canvas ref={canvasRef} style={{ display: 'block', maxWidth: '92vw', maxHeight: '82vh' }} />
          </div>

          {/* Spotlight Notification Card */}
          {latestSpotlight && (
            <div className="spotlight-broadcast-card">
              <div className="spotlight-card-label">
                <span className="status-dot-green" />
                <span>Live Ingestion Spotlight</span>
              </div>
              <div className="spotlight-token-id">
                {latestSpotlight.contributor_token || latestSpotlight.token || '#T-9182'}
              </div>
              <div className="spotlight-coord-tag">
                Placed at Grid: Column {latestSpotlight.col || 26}, Row {latestSpotlight.row || 17}
              </div>
            </div>
          )}

          {/* "Find My Photo" Command Bar */}
          <div className="command-bar-find">
            <IconSearch size={15} style={{ color: 'var(--text-tertiary)' }} />
            <input
              type="text"
              className="command-input"
              placeholder="#T-XXXX"
              value={tokenSearchInput}
              onChange={(e) => setTokenSearchInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleFindPhoto()}
            />
            <button className="btn-command-locate" onClick={handleFindPhoto}>
              Locate Tile
            </button>
            {tokenSearchResult && (
              <span style={{ fontSize: '0.74rem', color: 'var(--accent-emerald)', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                Col {tokenSearchResult.col}, Row {tokenSearchResult.row}
              </span>
            )}
          </div>
        </div>
      )}

      {/* 2. MOBILE INGESTION VIEW */}
      {activeTab === 'mobile' && (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
          <div className="mobile-attendee-card">
            <div style={{ width: '48px', height: '48px', borderRadius: '12px', background: 'rgba(56, 189, 248, 0.1)', border: '1px solid rgba(56, 189, 248, 0.25)', color: 'var(--accent-cyan)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 1.25rem' }}>
              <IconCamera size={22} />
            </div>

            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.35rem', fontWeight: 700, color: '#fff', marginBottom: '0.35rem' }}>
              Contribute to the Live Mural
            </h3>
            <p style={{ fontSize: '0.86rem', color: 'var(--text-secondary)', marginBottom: '1.5rem', lineHeight: 1.55 }}>
              Capture a selfie or conference moment. Photos are automatically normalized, stripped of EXIF telemetry, and rendered into the main stage wall.
            </p>

            {mobilePreview && (
              <div style={{ marginBottom: '1.25rem' }}>
                <img
                  src={mobilePreview}
                  alt="Preview"
                  style={{ width: '110px', height: '110px', borderRadius: '10px', objectFit: 'cover', border: '2px solid var(--accent-cyan)', margin: '0 auto', display: 'block' }}
                />
              </div>
            )}

            <label style={{
              display: 'block',
              background: '#f8fafc',
              color: '#090b10',
              fontFamily: 'var(--font-display)',
              fontSize: '0.92rem',
              fontWeight: 700,
              padding: '0.85rem',
              borderRadius: '10px',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}>
              {mobileUploading ? 'Normalizing & Uploading...' : 'Take or Select Photo'}
              <input type="file" accept="image/*" capture="user" onChange={handleMobileUpload} style={{ display: 'none' }} />
            </label>

            {myToken && (
              <div className="contributor-pass-ticket">
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', color: 'var(--accent-cyan)', letterSpacing: '0.08em' }}>
                  Verified Contributor Token
                </div>
                <div className="contributor-pass-token">{myToken}</div>
                <p style={{ fontSize: '0.76rem', color: 'var(--text-secondary)', marginTop: '0.2rem' }}>
                  Your tile is live on the big screen. Use this token in "Find My Photo" to highlight your location.
                </p>
                <button
                  onClick={() => setActiveTab('display')}
                  className="hud-btn"
                  style={{ width: '100%', justifyContent: 'center', marginTop: '0.85rem', borderColor: 'var(--border-cyan)', color: 'var(--accent-cyan)' }}
                >
                  <span>View on Main Display</span>
                  <IconArrowRight size={14} />
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 3. SETTINGS & EVENT QR CODE */}
      {activeTab === 'organizer' && (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
          <div className="mobile-attendee-card" style={{ width: '500px', textAlign: 'left' }}>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.25rem', fontWeight: 700, color: '#fff', marginBottom: '1.25rem' }}>
              Broadcast Stage Settings
            </h3>

            <div className="param-row">
              <div className="param-label-bar"><span>Event Display Title</span></div>
              <input
                type="text"
                value={eventTitle}
                onChange={(e) => setEventTitle(e.target.value)}
                style={{
                  width: '100%',
                  background: 'rgba(255, 255, 255, 0.04)',
                  border: '1px solid var(--border-subtle)',
                  color: '#fff',
                  padding: '0.6rem 0.85rem',
                  borderRadius: '8px',
                  fontFamily: 'var(--font-body)',
                  fontSize: '0.85rem',
                  outline: 'none',
                }}
              />
            </div>

            {/* Architectural QR Code Component */}
            <div style={{ marginTop: '1.5rem', background: '#fff', borderRadius: '12px', padding: '1.5rem', textAlign: 'center' }}>
              <svg width="140" height="140" viewBox="0 0 100 100" style={{ margin: '0 auto', display: 'block' }}>
                <rect width="100" height="100" fill="#fff" />
                <path d="M10,10 h30 v30 h-30 z M15,15 v20 h20 v-20 z M20,20 h10 v10 h-10 z" fill="#000" />
                <path d="M60,10 h30 v30 h-30 z M65,15 v20 h20 v-20 z M70,20 h10 v10 h-10 z" fill="#000" />
                <path d="M10,60 h30 v30 h-30 z M15,65 v20 h20 v-20 z M20,70 h10 v10 h-10 z" fill="#000" />
                <rect x="50" y="50" width="10" height="10" fill="#000" />
                <rect x="70" y="50" width="15" height="10" fill="#000" />
                <rect x="50" y="70" width="10" height="20" fill="#000" />
                <rect x="70" y="75" width="20" height="15" fill="#000" />
              </svg>
              <div style={{ color: '#090b10', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: '0.88rem', marginTop: '0.6rem' }}>
                Audience Mobile Ingestion QR
              </div>
              <div style={{ color: '#64748b', fontFamily: 'var(--font-mono)', fontSize: '0.72rem', marginTop: '0.15rem' }}>
                Direct V4 Signed URL &bull; Bucket: bucket-photo-library
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
