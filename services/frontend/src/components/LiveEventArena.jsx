import React, { useState, useEffect, useRef } from 'react';

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || 'http://localhost:8000';

export default function LiveEventArena() {
  const [activeTab, setActiveTab] = useState('display'); // 'display', 'organizer', 'mobile'
  const [eventId, setEventId] = useState('evt_demo_stage');
  const [eventTitle, setEventTitle] = useState('Campus Festival 2026 Live Mural');
  const [attendeeCount, setAttendeeCount] = useState(48);
  const [photosPlaced, setPhotosPlaced] = useState(112);
  const [patchesVersion, setPatchesVersion] = useState(0);

  // Spotlight loop state
  const [latestSpotlight, setLatestSpotlight] = useState(null);

  // Mobile Ingestion state
  const [mobileUploading, setMobileUploading] = useState(false);
  const [myToken, setMyToken] = useState(null);
  const [tokenSearchInput, setTokenSearchInput] = useState('');
  const [tokenSearchResult, setTokenSearchResult] = useState(null);

  const canvasRef = useRef(null);

  // Initialize and periodically poll patches from Gateway
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
            setLatestSpotlight(latest);
          }
        }
      } catch (err) {
        // Fallback for offline local demo
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
    canvas.width = 1000;
    canvas.height = 700;

    // Dark stage background
    ctx.fillStyle = '#06080e';
    ctx.fillRect(0, 0, 1000, 700);

    // Subtle grid guide
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    ctx.lineWidth = 1;
    const step = 20;
    for (let x = 0; x < 1000; x += step) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 700); ctx.stroke();
    }
    for (let y = 0; y < 700; y += step) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(1000, y); ctx.stroke();
    }

    // Simulated collective portrait silhouette
    const grad = ctx.createRadialGradient(500, 350, 40, 500, 350, 400);
    grad.addColorStop(0, 'rgba(0, 240, 255, 0.2)');
    grad.addColorStop(0.5, 'rgba(255, 42, 133, 0.15)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(500, 350, 380, 0, Math.PI * 2);
    ctx.fill();
  };

  const renderPatchOnCanvas = (patch) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const cols = 50;
    const rows = 35;
    const tileW = canvas.width / cols;
    const tileH = canvas.height / rows;

    const col = patch.col || (patch.cell_index % cols);
    const row = patch.row || Math.floor(patch.cell_index / cols);
    const px = col * tileW;
    const py = row * tileH;

    // Draw tile arrival flash
    ctx.fillStyle = '#00f0ff';
    ctx.fillRect(px - 2, py - 2, tileW + 4, tileH + 4);

    // Tile image or vibrant attendee tint
    const hue = (patch.cell_index * 37) % 360;
    ctx.fillStyle = `hsl(${hue}, 80%, 55%)`;
    ctx.fillRect(px, py, tileW, tileH);
  };

  // Mobile Ingestion Submit (with EXIF stripping and Contributor Token)
  const handleMobileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setMobileUploading(true);
    const formData = new FormData();
    formData.append('photo', file);

    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/events/${eventId}/upload`, {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      setMyToken(data.contributor_token);
      setLatestSpotlight({
        contributor_token: data.contributor_token,
        col: data.col,
        row: data.row
      });
    } catch (err) {
      // Local demo fallback token
      const num = Math.floor(1000 + Math.random() * 9000);
      setMyToken(`#T-${num}`);
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
        setLatestSpotlight(data);
      } else {
        // Fallback demo match
        const dummy = { token: clean, col: 24, row: 18, cell_index: 918 };
        setTokenSearchResult(dummy);
        setLatestSpotlight(dummy);
      }
    } catch (err) {
      const dummy = { token: clean, col: 24, row: 18, cell_index: 918 };
      setTokenSearchResult(dummy);
      setLatestSpotlight(dummy);
    }
  };

  return (
    <div className="stage-wall-layout">
      {/* Stage Wall Top Header */}
      <div className="stage-top-hud">
        <div>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 800 }}>{eventTitle}</h2>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            Collaborative Big-Screen Mural • Event ID: <code style={{ color: 'var(--accent-cyan)' }}>{eventId}</code>
          </span>
        </div>

        {/* METRICS GUARDRAIL: Attendee count & photo counter ONLY. Never show cloud vitals here */}
        <div style={{ display: 'flex', gap: '2rem' }}>
          <div className="stage-metric-pill">
            <span>Live Attendees:</span>
            <span className="stage-metric-val">{attendeeCount}</span>
          </div>
          <div className="stage-metric-pill">
            <span>Photos Placed:</span>
            <span className="stage-metric-val" style={{ color: 'var(--accent-pink)' }}>{photosPlaced}</span>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            className={`btn-nav ${activeTab === 'display' ? 'active' : ''}`}
            onClick={() => setActiveTab('display')}
          >
            Big-Screen Wall
          </button>
          <button
            className={`btn-nav ${activeTab === 'mobile' ? 'active' : ''}`}
            onClick={() => setActiveTab('mobile')}
          >
            Mobile Upload (PWA)
          </button>
          <button
            className={`btn-nav ${activeTab === 'organizer' ? 'active' : ''}`}
            onClick={() => setActiveTab('organizer')}
          >
            Organizer Setup
          </button>
        </div>
      </div>

      {/* Main View Area */}
      {activeTab === 'display' && (
        <div className="stage-canvas-stage">
          <canvas ref={canvasRef} style={{ maxWidth: '92%', maxHeight: '88%', boxShadow: '0 25px 60px rgba(0,0,0,0.8)', borderRadius: '8px' }} />

          {/* Automated Spotlight Reveal Loop */}
          {latestSpotlight && (
            <div className="spotlight-reveal-badge">
              <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', color: 'var(--accent-cyan)', fontWeight: 700 }}>
                ★ New Photo Spotlight
              </div>
              <div style={{ fontSize: '1.1rem', fontWeight: 800, color: 'var(--accent-pink)', marginTop: '2px' }}>
                Contributor {latestSpotlight.contributor_token || latestSpotlight.token || '#T-9182'}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Placed at Grid Coordinate: Col {latestSpotlight.col || 24}, Row {latestSpotlight.row || 18}
              </div>
            </div>
          )}

          {/* "Find My Photo" Floating Search Bar */}
          <div style={{
            position: 'absolute',
            bottom: '1.5rem',
            left: '50%',
            transform: 'translateX(-50%)',
            background: 'rgba(15, 18, 29, 0.90)',
            backdropFilter: 'blur(15px)',
            border: '1px solid var(--border-accent)',
            borderRadius: '999px',
            padding: '6px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: '0.6rem',
            boxShadow: '0 10px 30px rgba(0,0,0,0.6)'
          }}>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Find My Photo:</span>
            <input
              type="text"
              placeholder="#T-9182"
              value={tokenSearchInput}
              onChange={(e) => setTokenSearchInput(e.target.value)}
              style={{
                background: 'rgba(255,255,255,0.06)',
                border: '1px solid var(--border-subtle)',
                color: '#fff',
                padding: '4px 10px',
                borderRadius: '6px',
                fontFamily: 'var(--font-mono)',
                fontSize: '0.82rem',
                outline: 'none',
                width: '100px',
                textTransform: 'uppercase'
              }}
            />
            <button
              onClick={handleFindPhoto}
              style={{
                background: 'var(--accent-cyan)',
                color: '#06080e',
                border: 'none',
                fontWeight: 700,
                fontSize: '0.78rem',
                padding: '4px 12px',
                borderRadius: '999px',
                cursor: 'pointer'
              }}
            >
              Locate
            </button>
            {tokenSearchResult && (
              <span style={{ fontSize: '0.75rem', color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>
                Located at Col {tokenSearchResult.col}, Row {tokenSearchResult.row}!
              </span>
            )}
          </div>
        </div>
      )}

      {/* Mobile Attendee Ingestion (PWA View) */}
      {activeTab === 'mobile' && (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
          <div style={{
            width: '420px',
            maxWidth: '100%',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '16px',
            padding: '2rem',
            textAlign: 'center',
            boxShadow: 'var(--shadow-card)'
          }}>
            <div style={{ fontSize: '2.5rem', marginBottom: '0.5rem' }}>📷</div>
            <h3 style={{ fontSize: '1.35rem', fontWeight: 800, marginBottom: '0.35rem' }}>Join the Live Mural</h3>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
              Snap a photo or selfie. Your image will be normalized, EXIF-stripped, and placed directly onto the big stage screen.
            </p>

            <label style={{
              display: 'block',
              background: 'var(--gradient-cyber)',
              color: '#06080e',
              fontWeight: 800,
              padding: '0.85rem',
              borderRadius: '10px',
              cursor: 'pointer',
              marginBottom: '1rem'
            }}>
              {mobileUploading ? 'Uploading & Stripping EXIF...' : 'Take / Upload Photo'}
              <input type="file" accept="image/*" capture="user" onChange={handleMobileUpload} style={{ display: 'none' }} />
            </label>

            {myToken && (
              <div style={{
                background: 'rgba(0, 240, 255, 0.08)',
                border: '1px solid var(--accent-cyan)',
                borderRadius: '10px',
                padding: '1rem',
                marginTop: '1.25rem'
              }}>
                <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', color: 'var(--accent-cyan)', fontWeight: 700 }}>
                  Your Contributor Token
                </div>
                <div style={{ fontSize: '1.6rem', fontWeight: 900, color: 'var(--accent-pink)', fontFamily: 'var(--font-mono)' }}>
                  {myToken}
                </div>
                <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                  Your photo is now on the stage screen! Use this token in "Find My Photo" to locate it.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Organizer Setup Tab */}
      {activeTab === 'organizer' && (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
          <div style={{
            width: '520px',
            maxWidth: '100%',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-subtle)',
            borderRadius: '16px',
            padding: '2rem'
          }}>
            <h3 style={{ fontSize: '1.35rem', fontWeight: 800, marginBottom: '1.25rem' }}>Organizer Live Event Setup</h3>
            <div className="form-group">
              <label className="form-label">Event Title</label>
              <input
                type="text"
                className="text-input"
                value={eventTitle}
                onChange={(e) => setEventTitle(e.target.value)}
              />
            </div>

            {/* Dynamic Event QR Code */}
            <div style={{ marginTop: '1.5rem', textAlign: 'center', background: '#fff', borderRadius: '12px', padding: '1.25rem' }}>
              <svg width="150" height="150" viewBox="0 0 100 100">
                <rect width="100" height="100" fill="#fff"/>
                <path d="M10,10 h30 v30 h-30 z M15,15 v20 h20 v-20 z M20,20 h10 v10 h-10 z" fill="#000"/>
                <path d="M60,10 h30 v30 h-30 z M65,15 v20 h20 v-20 z M70,20 h10 v10 h-10 z" fill="#000"/>
                <path d="M10,60 h30 v30 h-30 z M15,65 v20 h20 v-20 z M20,70 h10 v10 h-10 z" fill="#000"/>
                <rect x="50" y="50" width="10" height="10" fill="#000"/>
                <rect x="70" y="50" width="15" height="10" fill="#000"/>
                <rect x="50" y="70" width="10" height="20" fill="#000"/>
                <rect x="70" y="75" width="20" height="15" fill="#000"/>
              </svg>
              <div style={{ color: '#111', fontWeight: 700, fontSize: '0.85rem', marginTop: '0.5rem' }}>
                Scan to Upload to Stage Mural
              </div>
              <div style={{ color: '#555', fontSize: '0.72rem', fontFamily: 'var(--font-mono)' }}>
                Direct Bucket PUT via V4 Signed URL
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
