import React from 'react';
import { IconCamera, IconGrid, IconCheck, IconArrowRight } from './Icons';

// The public home page, and the page search engines index for the whole site.
// Its two features are the product's two entry points.
export default function LandingPage({ onSelectPathway }) {
  // Real links (crawlable, open-in-new-tab works) that still navigate in-app on a normal click
  const go = (pathway) => (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    onSelectPathway(pathway);
  };

  return (
    <div className="landing-container">
      <div className="hero-status-badge">
        <span className="status-dot-green" />
        <span className="hero-status-text">Free photo mosaic maker &bull; Live event photo walls</span>
      </div>

      <h1 className="landing-title">
        Turn Thousands of Photos <br />
        <span>Into One Giant Photo Mosaic</span>
      </h1>

      <p className="landing-subtitle">
        PixelMosaic builds high-resolution photo mosaics from your own pictures and crowd-sourced photos,
        and powers live event walls where every guest's photo becomes a tile in a shared artwork, in real time.
      </p>

      <div className="pathway-grid">
        {/* FEATURE 1: LIVE EVENT ARENA */}
        <section className="pathway-card live" aria-labelledby="feature-live">
          <div>
            <div className="card-top">
              <div className="card-icon-frame">
                <IconCamera size={24} />
              </div>
              <span className="card-category-badge">For events &amp; venues</span>
            </div>

            <h2 id="feature-live" className="card-headline">Live Event Arena</h2>
            <p className="card-description">
              A live photo mosaic wall for festivals, weddings, conferences and stages. Guests scan a QR code and
              upload a photo from their phone; it appears on the big screen as part of the mosaic within seconds,
              with a spotlight on each new arrival.
            </p>

            <ul className="card-chip-list">
              <li className="card-chip">
                <IconCheck size={14} />
                <span>QR code &amp; phone uploads</span>
              </li>
              <li className="card-chip">
                <IconCheck size={14} />
                <span>Real-time big-screen reveal</span>
              </li>
              <li className="card-chip">
                <IconCheck size={14} />
                <span>Automatic spotlight on new photos</span>
              </li>
              <li className="card-chip">
                <IconCheck size={14} />
                <span>"Find My Photo" for every guest</span>
              </li>
            </ul>
          </div>

          <a
            id="btnEnterLive"
            href="/live"
            className="btn-action-primary btn-cyan-ghost"
            onClick={go('live')}
          >
            <span>Launch Live Event Arena</span>
            <IconArrowRight size={16} />
          </a>
        </section>

        {/* FEATURE 2: PHOTO MOSAIC STUDIO */}
        <section className="pathway-card studio" aria-labelledby="feature-studio">
          <div>
            <div className="card-top">
              <div className="card-icon-frame">
                <IconGrid size={24} />
              </div>
              <span className="card-category-badge">Photo mosaic maker</span>
            </div>

            <h2 id="feature-studio" className="card-headline">Photo Mosaic Studio</h2>
            <p className="card-description">
              Upload a picture and a folder of photos, and the studio rebuilds it as a mosaic using perceptual colour
              matching, with no repeated photos side by side. Mix your own photos with the community library, filter
              by theme, and export a print-ready image up to 12,000 pixels.
            </p>

            <ul className="card-chip-list">
              <li className="card-chip">
                <IconCheck size={14} />
                <span>Your photos, community photos, or both</span>
              </li>
              <li className="card-chip">
                <IconCheck size={14} />
                <span>Accurate perceptual colour matching</span>
              </li>
              <li className="card-chip">
                <IconCheck size={14} />
                <span>No-repeat tile placement</span>
              </li>
              <li className="card-chip">
                <IconCheck size={14} />
                <span>High-res PNG &amp; JPEG export</span>
              </li>
            </ul>
          </div>

          <a
            id="btnEnterStudio"
            href="/studio"
            className="btn-action-primary btn-rose-ghost"
            onClick={go('studio')}
          >
            <span>Open Photo Mosaic Studio</span>
            <IconArrowRight size={16} />
          </a>
        </section>
      </div>
    </div>
  );
}
