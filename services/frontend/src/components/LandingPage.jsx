import React from 'react';

export default function LandingPage({ onSelectPathway }) {
  return (
    <div className="landing-hero">
      <div className="hero-badge">Collaborative Digital Art & High-Compute Raster Platform</div>
      <h1 className="hero-title">Transform Collective Memories Into High-Density Art</h1>
      <p className="hero-subtitle">
        A distributed, bucket-only visual platform processing thousands of crowd photos into iconic multi-scale mosaics, ASCII murals, and stained-glass Voronoi canvases.
      </p>

      {/* Strict Requirement: ONLY TWO CARDS. Zero developer test buttons, zero synthetic preset generators */}
      <div className="hero-cards-grid">
        {/* OPTION 1: LIVE EVENT ARENA */}
        <div className="pathway-card live">
          <div>
            <div className="pathway-icon">🎪</div>
            <h2 className="pathway-title">Option 1: Live Event Arena</h2>
            <p className="pathway-desc">
              Launch or join a collaborative live mural for an event, concert, or festival. Display dynamic progress on big screens as crowd photos stream in via instant QR code upload with real-time spotlight reveals and the "Find My Photo" locator.
            </p>
          </div>
          <button
            id="btnEnterLive"
            className="btn-pathway"
            onClick={() => onSelectPathway('live')}
          >
            Launch Live Event Arena &rarr;
          </button>
        </div>

        {/* OPTION 2: PHOTO MOSAIC STUDIO */}
        <div className="pathway-card studio">
          <div>
            <div className="pathway-icon">🎨</div>
            <h2 className="pathway-title">Option 2: Photo Mosaic Studio</h2>
            <p className="pathway-desc">
              Enter the full creative workstation to render high-resolution mosaics, ASCII murals, Voronoi paintings, and gigapixel zoom canvases using crowd-sourced or personal image archives with precision parameter tuning.
            </p>
          </div>
          <button
            id="btnEnterStudio"
            className="btn-pathway"
            onClick={() => onSelectPathway('studio')}
          >
            Open Studio Workshop &rarr;
          </button>
        </div>
      </div>
    </div>
  );
}
