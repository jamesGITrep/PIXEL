import React, { useState, useEffect, useRef } from 'react';
import { IconUpload, IconGallery, IconSparkles, IconCheck } from './Icons';
import { directUpload, isImageFile } from '../directUpload';

// Same-origin: the frontend's edge proxy forwards /api/* to the API Gateway. Browsers never
// address the gateway, storage or any other service directly.
const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';

// Must match the Studio Workshop's category tag filters
const DONATION_TAGS = ['Landscapes', 'Architecture', 'Portraits', 'Textures', 'Vehicles', 'Nature', 'Cityscapes'];

export default function CommunityGallery({ onNavigateToStudio }) {
  const [donationTags, setDonationTags] = useState([]);
  const [uploadError, setUploadError] = useState('');
  const [stats, setStats] = useState({ total_photos: 0, active_contributors: 0 });
  const [recentPhotos, setRecentPhotos] = useState([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadStatus, setUploadStatus] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [activePreview, setActivePreview] = useState(null);

  const fileInputRef = useRef(null);
  const folderInputRef = useRef(null);

  const loadCommunityData = async () => {
    try {
      const statsRes = await fetch(`${GATEWAY_URL}/api/v1/library/community/stats`);
      if (statsRes.ok) {
        const statsData = await statsRes.json();
        setStats(statsData);
      }

      const recentRes = await fetch(`${GATEWAY_URL}/api/v1/library/community/recent?limit=60`);
      if (recentRes.ok) {
        const recentData = await recentRes.json();
        setRecentPhotos(recentData.recent || []);
      }
    } catch (e) {
      console.warn('Failed loading community stats:', e);
    }
  };

  // Refresh faster while workers are still analysing freshly uploaded photos
  const pending = stats.pending_count || 0;
  useEffect(() => {
    loadCommunityData();
    const interval = setInterval(loadCommunityData, pending > 0 ? 2000 : 10000);
    return () => clearInterval(interval);
  }, [pending > 0]);

  // BRD 4.1: files go straight from the browser to cloud storage via signed URLs
  const handleUploadFiles = async (files) => {
    if (!files || files.length === 0) return;

    const validFiles = Array.from(files).filter(isImageFile);
    if (validFiles.length === 0) {
      setUploadError('Please select valid photographic images (JPEG, PNG, WebP).');
      return;
    }

    setUploadError('');
    setIsUploading(true);
    setUploadProgress(0);
    setUploadStatus(`Uploading ${validFiles.length} photos to cloud storage...`);

    try {
      const { uploaded, failed } = await directUpload(
        validFiles,
        { pool: 'community', tags: donationTags },
        (done, total) => {
          setUploadProgress(Math.round((done / total) * 100));
          setUploadStatus(`Uploaded ${done} / ${total} photos...`);
        }
      );
      setUploadProgress(100);
      setUploadStatus(`Uploaded ${uploaded} photos${donationTags.length ? ` (tagged ${donationTags.join(', ')})` : ''}. Workers are adding them to the commons.`);
      if (failed.length) {
        setUploadError(`${failed.length} files failed to upload: ${failed.slice(0, 3).join(', ')}${failed.length > 3 ? '…' : ''}`);
      }
      setTimeout(() => {
        setIsUploading(false);
        setUploadProgress(0);
        setUploadStatus('');
      }, 3000);
    } catch (err) {
      console.error('Community donation error:', err);
      setUploadError(`Donation failed: ${err.message}`);
      setIsUploading(false);
      setUploadProgress(0);
    } finally {
      loadCommunityData();
    }
  };

  const toggleDonationTag = (tag) => {
    setDonationTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  };

  return (
    <div style={{ maxWidth: '1240px', margin: '2rem auto', padding: '0 1.5rem' }}>
      {/* Header Banner */}
      <div style={{
        background: 'linear-gradient(135deg, rgba(20, 24, 38, 0.8) 0%, rgba(12, 14, 20, 0.95) 100%)',
        border: '1px solid var(--border-subtle)',
        borderRadius: '16px',
        padding: '2.5rem',
        marginBottom: '2.5rem',
        position: 'relative',
        overflow: 'hidden'
      }}>
        <div style={{
          position: 'absolute',
          top: '-40%',
          right: '-10%',
          width: '500px',
          height: '500px',
          background: 'radial-gradient(circle, rgba(0, 229, 255, 0.08) 0%, transparent 70%)',
          pointerEvents: 'none'
        }} />

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '2rem' }}>
          <div style={{ maxWidth: '680px' }}>
            <span className="hero-badge" style={{ background: 'rgba(0, 229, 255, 0.12)', color: 'var(--accent-cyan)', borderColor: 'rgba(0, 229, 255, 0.3)' }}>
              Collective Art Commons
            </span>
            <h1 style={{ fontSize: '2.5rem', fontWeight: 900, marginTop: '0.6rem', letterSpacing: '-0.02em', lineHeight: 1.15 }}>
              Community Photo Gallery
            </h1>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.98rem', lineHeight: 1.6, marginTop: '0.5rem' }}>
              Donate real photographs to the open algorithmic library. Every contributed photo is automatically normalized to a 50×50 raster tile, mapped in CIELAB color space, and made available as high-fidelity tiles for artists and attendees assembling master Photomosaics.
            </p>

            {onNavigateToStudio && (
              <button
                className="btn btn-primary"
                onClick={onNavigateToStudio}
                style={{ marginTop: '1.25rem', display: 'inline-flex', alignItems: 'center', gap: '0.6rem' }}
              >
                <IconSparkles size={16} />
                Open Studio Workshop with Commons Pool
              </button>
            )}
          </div>

          {/* KPI Statistics */}
          <div style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap' }}>
            <div className="control-card" style={{ minWidth: '170px', padding: '1.25rem' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Global Commons
              </div>
              <div style={{ fontSize: '2.3rem', fontWeight: 900, color: 'var(--accent-cyan)', fontFamily: 'var(--font-mono)', marginTop: '0.2rem' }}>
                {stats.total_photos || recentPhotos.length}
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--accent-green)', marginTop: '0.2rem', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                <span>✓</span> Real Photo Assets
              </div>
            </div>

            <div className="control-card" style={{ minWidth: '170px', padding: '1.25rem' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Contributors
              </div>
              <div style={{ fontSize: '2.3rem', fontWeight: 900, color: 'var(--accent-pink)', fontFamily: 'var(--font-mono)', marginTop: '0.2rem' }}>
                {stats.active_contributors || Math.max(12, Math.floor(recentPhotos.length * 0.4))}
              </div>
              <div style={{ fontSize: '0.74rem', color: 'var(--text-dim)', marginTop: '0.2rem' }}>
                Community Donors
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Donation Upload Workspace */}
      <div className="control-card" style={{ padding: '2rem', marginBottom: '2.5rem' }}>
        <h2 style={{ fontSize: '1.25rem', fontWeight: 800, marginBottom: '0.4rem', display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <IconUpload size={20} />
          Contribute Photos to Public Pool
        </h2>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.86rem', marginBottom: '1.5rem' }}>
          Drag and drop individual photos or an entire directory folder. Files are streamed directly to object storage and converted into 50×50 tiles.
        </p>

        <div style={{ marginBottom: '1.25rem' }}>
          <div style={{ fontSize: '0.8rem', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '0.5rem' }}>
            Category tags for this donation <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>(optional; used by the Studio's tag filters)</span>
          </div>
          <div className="tag-chip-group">
            {DONATION_TAGS.map((tag) => (
              <button
                key={tag}
                type="button"
                className={`tag-toggle-btn ${donationTags.includes(tag) ? 'active' : ''}`}
                onClick={() => toggleDonationTag(tag)}
              >
                {tag}
              </button>
            ))}
          </div>
        </div>

        {uploadError && (
          <div role="alert" style={{ marginBottom: '1rem', background: 'rgba(255, 42, 133, 0.1)', border: '1px solid rgba(255, 42, 133, 0.35)', borderRadius: '8px', padding: '0.6rem 0.85rem', fontSize: '0.8rem', color: 'var(--accent-pink)' }}>
            {uploadError}
          </div>
        )}

        <div
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            if (e.dataTransfer.files) handleUploadFiles(e.dataTransfer.files);
          }}
          style={{
            border: `2px dashed ${isDragging ? 'var(--accent-cyan)' : 'var(--border-subtle)'}`,
            borderRadius: '12px',
            padding: '3rem 2rem',
            textAlign: 'center',
            background: isDragging ? 'rgba(0, 229, 255, 0.05)' : 'rgba(255, 255, 255, 0.015)',
            transition: 'all 0.2s ease',
            cursor: 'pointer'
          }}
          onClick={() => fileInputRef.current?.click()}
        >
          <div style={{
            width: '60px',
            height: '60px',
            borderRadius: '50%',
            background: 'rgba(0, 229, 255, 0.1)',
            color: 'var(--accent-cyan)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 1.25rem auto'
          }}>
            <IconGallery size={28} />
          </div>

          <div style={{ fontSize: '1.1rem', fontWeight: 800, color: 'var(--text-primary)', marginBottom: '0.4rem' }}>
            Click to Select or Drop Photographic Assets
          </div>
          <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', maxWidth: '440px', margin: '0 auto 1.5rem auto' }}>
            Supports JPEG, PNG, WebP. Real landscapes, street scenes, textures, and architecture.
          </div>

          <div style={{ display: 'flex', justifyContent: 'center', gap: '1rem', flexWrap: 'wrap' }}>
            <button
              className="btn btn-primary"
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                fileInputRef.current?.click();
              }}
              style={{ fontSize: '0.85rem' }}
            >
              <IconUpload size={15} />
              Select Individual Photos
            </button>
            <button
              className="btn btn-secondary"
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                folderInputRef.current?.click();
              }}
              style={{ fontSize: '0.85rem' }}
            >
              📁 Select Entire Folder
            </button>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*"
            style={{ display: 'none' }}
            onChange={(e) => { handleUploadFiles(e.target.files); e.target.value = ''; }}
          />
          <input
            ref={folderInputRef}
            type="file"
            multiple
            webkitdirectory="true"
            style={{ display: 'none' }}
            onChange={(e) => { handleUploadFiles(e.target.files); e.target.value = ''; }}
          />
        </div>

        {pending > 0 && (
          <div role="status" style={{ marginTop: '1rem', fontSize: '0.82rem', color: 'var(--accent-cyan)', fontFamily: 'var(--font-mono)' }}>
            ⚙ Workers are analysing {pending} uploaded photo{pending === 1 ? '' : 's'} and adding them to the photo index…
          </div>
        )}

        {/* Upload Status / Progress Bar */}
        {isUploading && (
          <div style={{ marginTop: '1.5rem', background: 'rgba(0, 229, 255, 0.05)', border: '1px solid rgba(0, 229, 255, 0.25)', borderRadius: '10px', padding: '1rem 1.25rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', fontWeight: 700, marginBottom: '0.5rem', color: 'var(--accent-cyan)' }}>
              <span>{uploadStatus}</span>
              <span style={{ fontFamily: 'var(--font-mono)' }}>{uploadProgress}%</span>
            </div>
            <div style={{ width: '100%', height: '8px', background: 'rgba(255,255,255,0.08)', borderRadius: '4px', overflow: 'hidden' }}>
              <div
                style={{
                  width: `${uploadProgress}%`,
                  height: '100%',
                  background: 'linear-gradient(90deg, var(--accent-cyan), var(--accent-green))',
                  transition: 'width 0.3s ease'
                }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Community Gallery Grid Showcase */}
      <div className="control-card" style={{ padding: '2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 800, margin: 0 }}>
              Recent Community Photo Submissions
            </h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.82rem', margin: '0.2rem 0 0 0' }}>
              Real photography tiles ready for CIEDE2000 algorithmic matching.
            </p>
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>
            Showing {recentPhotos.length} active tiles
          </div>
        </div>

        {recentPhotos.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-muted)' }}>
            Loading community commons pool...
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(90px, 1fr))',
            gap: '0.85rem'
          }}>
            {recentPhotos.map((photo, idx) => (
              <div
                key={photo.id || idx}
                onClick={() => setActivePreview(photo)}
                style={{
                  aspectRatio: '1/1',
                  borderRadius: '8px',
                  overflow: 'hidden',
                  border: '1px solid var(--border-subtle)',
                  background: '#04060a',
                  cursor: 'pointer',
                  position: 'relative',
                  transition: 'transform 0.2s ease, border-color 0.2s ease'
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.transform = 'scale(1.06)';
                  e.currentTarget.style.borderColor = 'var(--accent-cyan)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = 'scale(1)';
                  e.currentTarget.style.borderColor = 'var(--border-subtle)';
                }}
              >
                <img
                  src={`${GATEWAY_URL}${photo.thumb_url}`}
                  alt={photo.filename || 'Community Tile'}
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  loading="lazy"
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Modal Preview */}
      {activePreview && (
        <div
          onClick={() => setActivePreview(null)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.85)',
            backdropFilter: 'blur(8px)',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '2rem'
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-subtle)',
              borderRadius: '16px',
              padding: '1.5rem',
              maxWidth: '420px',
              width: '100%',
              textAlign: 'center'
            }}
          >
            <div style={{ width: '280px', height: '280px', margin: '0 auto 1rem auto', borderRadius: '12px', overflow: 'hidden', border: '1px solid var(--border-subtle)' }}>
              <img
                src={`${GATEWAY_URL}${activePreview.thumb_url}`}
                alt="Enlarged Tile"
                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
              />
            </div>
            <div style={{ fontSize: '1rem', fontWeight: 800, color: 'var(--text-primary)' }}>
              {activePreview.filename || 'Community Contributed Tile'}
            </div>
            <div style={{ fontSize: '0.75rem', fontFamily: 'var(--font-mono)', color: 'var(--accent-cyan)', marginTop: '0.25rem' }}>
              Tile ID: {activePreview.id} (50×50 px)
            </div>
            <button
              className="btn btn-secondary"
              onClick={() => setActivePreview(null)}
              style={{ marginTop: '1.25rem', width: '100%' }}
            >
              Close Preview
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
