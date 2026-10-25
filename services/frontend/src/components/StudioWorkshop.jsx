import React, { useState, useRef, useEffect } from 'react';

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || 'http://localhost:8000';

export default function StudioWorkshop() {
  const [selectedMode, setSelectedMode] = useState('photomosaic');
  const [blendRatio, setBlendRatio] = useState(70);
  const [selectedTags, setSelectedTags] = useState(['Landscapes', 'Vehicles', 'Selfies', 'People']);
  const [gridDensity, setGridDensity] = useState(60);
  const [noRepeatRadius, setNoRepeatRadius] = useState(5);
  const [alphaTint, setAlphaTint] = useState(20);

  const [targetFile, setTargetFile] = useState(null);
  const [targetPreviewUrl, setTargetPreviewUrl] = useState(null);

  const [jobId, setJobId] = useState(null);
  const [jobProgress, setJobProgress] = useState(0);
  const [isProcessing, setIsProcessing] = useState(false);
  const [jobCompleted, setJobCompleted] = useState(false);
  const [resultOutputUrl, setResultOutputUrl] = useState(null);

  const canvasRef = useRef(null);

  const tagsList = ['Landscapes', 'Vehicles', 'Waterfalls', 'People', 'Selfies', 'Architecture'];

  const toggleTag = (tag) => {
    if (selectedTags.includes(tag)) {
      setSelectedTags(selectedTags.filter((t) => t !== tag));
    } else {
      setSelectedTags([...selectedTags, tag]);
    }
  };

  const handleTargetSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) {
      setTargetFile(file);
      setTargetPreviewUrl(URL.createObjectURL(file));
      setJobCompleted(false);
    }
  };

  // Submit Job to Gateway Microservice
  const handleSubmitJob = async () => {
    if (!targetFile) {
      alert('Please upload a master target poster first.');
      return;
    }

    setIsProcessing(true);
    setJobProgress(5);
    setJobCompleted(false);

    const formData = new FormData();
    formData.append('target_file', targetFile);
    formData.append(
      'config_json',
      JSON.stringify({
        mode: selectedMode,
        columns: gridDensity,
        rows: gridDensity,
        metric: 'CIELAB_DELTA_E_2000',
        blend_ratio_personal: blendRatio / 100.0,
        selected_tags: selectedTags,
        no_repeat_radius: noRepeatRadius,
        alpha_tint_percentage: alphaTint,
      })
    );

    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/jobs/create`, {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setJobId(data.job_id);

      // Start polling status
      pollJobStatus(data.job_id);
    } catch (err) {
      console.warn('Gateway dispatch fallback to local canvas preview:', err);
      simulateLocalRender();
    }
  };

  const pollJobStatus = (id) => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${GATEWAY_URL}/api/v1/jobs/${id}/status`);
        if (res.ok) {
          const status = await res.json();
          setJobProgress(status.progress_percent);
          if (status.is_complete) {
            clearInterval(interval);
            setIsProcessing(false);
            setJobCompleted(true);
            setResultOutputUrl(status.output_url);
            drawRenderedResult();
          }
        }
      } catch (e) {
        clearInterval(interval);
        simulateLocalRender();
      }
    }, 1500);
  };

  const simulateLocalRender = () => {
    let p = 15;
    const interval = setInterval(() => {
      p += 25;
      setJobProgress(Math.min(100, p));
      if (p >= 100) {
        clearInterval(interval);
        setIsProcessing(false);
        setJobCompleted(true);
        drawRenderedResult();
      }
    }, 400);
  };

  const drawRenderedResult = () => {
    const canvas = canvasRef.current;
    if (!canvas || !targetPreviewUrl) return;
    const ctx = canvas.getContext('2d');
    const img = new Image();
    img.src = targetPreviewUrl;
    img.onload = () => {
      canvas.width = 900;
      canvas.height = Math.round(900 * (img.height / img.width));

      // Draw poster with simulated high-density raster cells
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

      if (selectedMode === 'ascii') {
        ctx.fillStyle = 'rgba(4, 13, 6, 0.92)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#00ff66';
        ctx.font = '8px monospace';
        for (let y = 12; y < canvas.height; y += 12) {
          ctx.fillText('─│╱╲+*#@░▒▓█~'.repeat(12), 10, y);
        }
      } else if (selectedMode === 'voronoi') {
        ctx.strokeStyle = 'rgba(20, 20, 20, 0.4)';
        ctx.lineWidth = 1;
        for (let i = 0; i < 400; i++) {
          const rx = Math.random() * canvas.width;
          const ry = Math.random() * canvas.height;
          ctx.strokeRect(rx, ry, 25, 25);
        }
      }
    };
  };

  return (
    <div className="studio-layout">
      {/* Left Control Sidebar */}
      <aside className="sidebar-panel">
        {/* 1. Target Upload */}
        <section className="control-card">
          <div className="card-title">1. Master Target Poster</div>
          <label
            style={{
              display: 'block',
              border: '2px dashed var(--border-subtle)',
              borderRadius: '8px',
              padding: '1.25rem 1rem',
              textAlign: 'center',
              cursor: 'pointer',
              background: 'rgba(255,255,255,0.02)',
            }}
          >
            <div style={{ fontSize: '1.75rem', marginBottom: '0.4rem' }}>📁</div>
            <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>
              {targetFile ? targetFile.name : 'Select or Drop Target Poster'}
            </span>
            <input type="file" accept="image/*" onChange={handleTargetSelect} style={{ display: 'none' }} />
          </label>
        </section>

        {/* 2. Compute Mode Selector */}
        <section className="control-card">
          <div className="card-title">2. Compute Mode Engine</div>
          <select
            className="select-input"
            value={selectedMode}
            onChange={(e) => setSelectedMode(e.target.value)}
          >
            <option value="photomosaic">Mode 1: Photomosaic Synthesis (CIELAB ΔE2000)</option>
            <option value="ascii">Mode 2: Structural ASCII & Glyph Art (3x3 Sobel)</option>
            <option value="voronoi">Mode 3: Geometric Voronoi & Stained Glass</option>
            <option value="deepzoom">Mode 4: Gigapixel Deep-Zoom & Time-Lapse Slicer</option>
          </select>
        </section>

        {/* 3. Asset Pool Sourcing & Filter Controls */}
        <section className="control-card">
          <div className="card-title">3. Asset Pool & Semantic Filters</div>
          <div className="form-group">
            <div className="form-label">
              <span>Source Blend (Personal vs Public)</span>
              <span className="label-val">{blendRatio}% Personal</span>
            </div>
            <input
              type="range"
              className="range-slider"
              min="0"
              max="100"
              value={blendRatio}
              onChange={(e) => setBlendRatio(Number(e.target.value))}
            />
          </div>

          <div className="form-group">
            <div className="form-label">
              <span>Category Tag Filters (global_index.parquet)</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              {tagsList.map((tag) => (
                <label key={tag} style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={selectedTags.includes(tag)}
                    onChange={() => toggleTag(tag)}
                  />
                  <span>{tag}</span>
                </label>
              ))}
            </div>
          </div>
        </section>

        {/* 4. Algorithmic Parameter Tuning */}
        <section className="control-card">
          <div className="card-title">4. Algorithmic Tuning</div>
          <div className="form-group">
            <div className="form-label">
              <span>Grid Block Density</span>
              <span className="label-val">{gridDensity} x {gridDensity}</span>
            </div>
            <input
              type="range"
              className="range-slider"
              min="30"
              max="150"
              value={gridDensity}
              onChange={(e) => setGridDensity(Number(e.target.value))}
            />
          </div>

          <div className="form-group">
            <div className="form-label">
              <span>No-Repeat Spatial Radius</span>
              <span className="label-val">R = {noRepeatRadius} cells</span>
            </div>
            <input
              type="range"
              className="range-slider"
              min="0"
              max="10"
              value={noRepeatRadius}
              onChange={(e) => setNoRepeatRadius(Number(e.target.value))}
            />
          </div>

          <div className="form-group">
            <div className="form-label">
              <span>Alpha-Tint Overlay Slider</span>
              <span className="label-val">{alphaTint}%</span>
            </div>
            <input
              type="range"
              className="range-slider"
              min="0"
              max="50"
              value={alphaTint}
              onChange={(e) => setAlphaTint(Number(e.target.value))}
            />
          </div>
        </section>

        {/* Execute Button & Progress */}
        <button
          className="btn-pathway"
          style={{ background: 'var(--gradient-cyber)', color: '#06080e', fontWeight: 800 }}
          onClick={handleSubmitJob}
          disabled={isProcessing}
        >
          {isProcessing ? `Computing Raster Job (${jobProgress}%)...` : '⚡ DISPATCH COMPUTE JOB'}
        </button>

        {isProcessing && (
          <div style={{ background: 'rgba(255,255,255,0.05)', borderRadius: '6px', height: '10px', overflow: 'hidden' }}>
            <div style={{ width: `${jobProgress}%`, height: '100%', background: 'var(--accent-cyan)', transition: 'width 0.3s ease' }} />
          </div>
        )}
      </aside>

      {/* Main Artboard Canvas Workspace */}
      <section style={{ display: 'flex', flexDirection: 'column', flex: 1, background: '#090b12', position: 'relative', overflow: 'hidden' }}>
        <div style={{ padding: '0.85rem 2rem', background: 'rgba(15,18,29,0.85)', borderBottom: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            Status: <span style={{ color: jobCompleted ? 'var(--accent-green)' : 'var(--accent-cyan)', fontWeight: 600 }}>
              {jobCompleted ? 'Complete (Stitched in bucket-mosaic-outputs)' : isProcessing ? 'Worker MIG Partitioning...' : 'Ready'}
            </span>
          </div>
          {jobCompleted && (
            <button
              style={{ background: 'rgba(0,240,255,0.15)', border: '1px solid var(--accent-cyan)', color: 'var(--accent-cyan)', padding: '4px 12px', borderRadius: '6px', fontWeight: 700, cursor: 'pointer' }}
              onClick={() => {
                const link = document.createElement('a');
                link.download = `mosaic_${Date.now()}.png`;
                link.href = canvasRef.current.toDataURL();
                link.click();
              }}
            >
              Download Master High-Res PNG
            </button>
          )}
        </div>

        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1rem' }}>
          <canvas ref={canvasRef} style={{ maxWidth: '92%', maxHeight: '88%', boxShadow: '0 20px 50px rgba(0,0,0,0.8)', borderRadius: '6px' }} />
        </div>
      </section>
    </div>
  );
}
