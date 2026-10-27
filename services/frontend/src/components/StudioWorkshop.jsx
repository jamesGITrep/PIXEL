import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  IconGrid,
  IconAscii,
  IconBrush,
  IconFlow,
  IconUpload,
  IconDownload,
  IconSparkles,
  IconTrash,
  IconGallery,
  IconCheck,
} from './Icons';
import { directUpload, isImageFile } from '../directUpload';

// Same-origin: the frontend's edge proxy forwards /api/* to the API Gateway. Browsers never
// address the gateway, storage or any other service directly.
const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';

// Live-progress canvas is drawn at display scale; the full-resolution artboard is only downloaded on export.
const LIVE_CANVAS_MAX_EDGE = 2400;
const DETAIL_ZOOM = 2;            // above 200%, visible tiles are overlaid from their 256px originals
const DETAIL_MAX_TILES = 900;     // cap on simultaneously mounted detail tiles
const FLY_DURATION = 0.75;        // seconds one tile spends in flight
const RIPPLE_DURATION = 0.4;      // seconds of the dock glow

// CSS cubic-bezier(x1, y1, x2, y2) as a JS easing function (Newton's method with bisection fallback)
const cubicBezier = (x1, y1, x2, y2) => {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = (t) => ((ax * t + bx) * t + cx) * t;
  const sy = (t) => ((ay * t + by) * t + cy) * t;
  const dx = (t) => (3 * ax * t + 2 * bx) * t + cx;
  return (x) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let t = x;
    for (let i = 0; i < 8; i += 1) {
      const err = sx(t) - x;
      if (Math.abs(err) < 1e-5) return sy(t);
      const d = dx(t);
      if (Math.abs(d) < 1e-6) break;
      t -= err / d;
    }
    let lo = 0, hi = 1;
    t = x;
    for (let i = 0; i < 24; i += 1) {
      if (sx(t) < x) lo = t; else hi = t;
      t = (lo + hi) / 2;
    }
    return sy(t);
  };
};
const easeFlyIn = cubicBezier(0.16, 1, 0.3, 1);

// 256px tile for a thumbnail URL; legacy commons photos at the bucket root have only the thumbnail
const tileUrlOf = (thumb) => thumb.replace(/^(\/api\/v1\/storage\/[^/]+\/.+\/)thumbs-50x50\//, '$1tiles-256/');

const matchLabel = (de) => {
  if (de === null || de === undefined) return '';
  if (de <= 2) return 'Excellent match (barely perceptible)';
  if (de <= 5) return 'Good match';
  if (de <= 10) return 'Fair match';
  return 'Loose match';
};

async function readError(res) {
  try {
    const body = await res.json();
    return body.detail || `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

function Notice({ notice, onDismiss }) {
  if (!notice) return null;
  const palette = {
    error: { bg: 'rgba(255, 42, 133, 0.1)', border: 'rgba(255, 42, 133, 0.4)', fg: 'var(--accent-pink)' },
    success: { bg: 'rgba(0, 255, 163, 0.08)', border: 'rgba(0, 255, 163, 0.35)', fg: 'var(--accent-green)' },
    info: { bg: 'rgba(0, 229, 255, 0.08)', border: 'rgba(0, 229, 255, 0.35)', fg: 'var(--accent-cyan)' },
  }[notice.type || 'info'];
  return (
    <div
      role={notice.type === 'error' ? 'alert' : 'status'}
      style={{
        display: 'flex',
        gap: '0.5rem',
        alignItems: 'flex-start',
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        color: palette.fg,
        borderRadius: '8px',
        padding: '0.55rem 0.75rem',
        fontSize: '0.74rem',
        lineHeight: 1.4,
        marginBottom: '0.75rem'
      }}
    >
      <span style={{ flex: 1 }}>{notice.text}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        style={{ background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', fontSize: '0.85rem', lineHeight: 1 }}
      >
        &times;
      </button>
    </div>
  );
}

export default function StudioWorkshop({ user, onRequestSignIn, onNavigateToGallery }) {
  const [selectedMode, setSelectedMode] = useState('photomosaic');

  // Mathematical Parameters
  const [gridDensity, setGridDensity] = useState(60);
  const [noRepeatRadius, setNoRepeatRadius] = useState(5);
  const [alphaTint, setAlphaTint] = useState(20);
  // Empty selection = no tag filtering (BRD 4.3 filter is strict once tags are picked)
  const [selectedTags, setSelectedTags] = useState([]);

  // Pool Source Selection + BRD 4.2 personal/community blend ratio
  const [usePersonalPhotos, setUsePersonalPhotos] = useState(true);
  const [useCommunityPhotos, setUseCommunityPhotos] = useState(true);
  const [blendPersonalPct, setBlendPersonalPct] = useState(50);
  const [uploadTags, setUploadTags] = useState([]);
  const [isVaultDragging, setIsVaultDragging] = useState(false);
  const [vaultPending, setVaultPending] = useState(0);
  const [communityCount, setCommunityCount] = useState(0);
  const [communityFilteredCount, setCommunityFilteredCount] = useState(0);
  const [communityTagCounts, setCommunityTagCounts] = useState({});

  // Inline feedback (window.alert/confirm are blocked in embedded/sandboxed browsers)
  const [vaultNotice, setVaultNotice] = useState(null);
  const [jobNotice, setJobNotice] = useState(null);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [isClearingVault, setIsClearingVault] = useState(false);

  // Personal Photo Vault. Signed in: bound to the account (personal/{user_id}/), so it follows the
  // user across sessions and devices. Guests: a temporary vault for this browser session only,
  // deleted once they leave (moved into the account if they sign in first).
  const isGuest = user === null;
  const vaultKey = user === undefined ? null : (user?.user_id || 'guest'); // null while sign-in is being checked
  const usePersonal = usePersonalPhotos;
  const [personalPhotos, setPersonalPhotos] = useState([]);
  const [isVaultUploading, setIsVaultUploading] = useState(false);
  const [vaultUploadProgress, setVaultUploadProgress] = useState(0);
  const [vaultUploadStatus, setVaultUploadStatus] = useState('');

  // ASCII Engine Parameters
  const [asciiTheme, setAsciiTheme] = useState('cyan');
  const [asciiRamp, setAsciiRamp] = useState('extended');

  // Kuwahara Engine Parameters
  const [kuwaharaRadius, setKuwaharaRadius] = useState(4);

  // Flow Field Engine Parameters
  const [stepLength, setStepLength] = useState(15);
  const [numParticles, setNumParticles] = useState(5000);
  const [strokeWidth, setStrokeWidth] = useState(2);

  // Target Upload & Previews
  const [targetFile, setTargetFile] = useState(null);
  const [targetPreviewUrl, setTargetPreviewUrl] = useState(null);
  const [imageMeta, setImageMeta] = useState(null);
  const [isDragging, setIsDragging] = useState(false);

  // Live Job Pipeline State
  const [jobId, setJobId] = useState(null);
  const [jobProgress, setJobProgress] = useState(0);
  const [completedChunks, setCompletedChunks] = useState(0);
  const [totalChunks, setTotalChunks] = useState(0);
  const [isProcessing, setIsProcessing] = useState(false);
  const [jobCompleted, setJobCompleted] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');
  const [finalMosaicUrl, setFinalMosaicUrl] = useState(null);
  const [canvasReady, setCanvasReady] = useState(false);
  const [exportResolution, setExportResolution] = useState(4000);
  const [assemblyPhase, setAssemblyPhase] = useState(null); // null | 'assembling'
  const [detailTiles, setDetailTiles] = useState([]);
  const [inspect, setInspect] = useState(null); // { cell, loading, data?, error? }
  const [exportFormat, setExportFormat] = useState('png');
  const [exportState, setExportState] = useState(null); // { busy, text, pct?, done?, error? }
  const [lastJobDims, setLastJobDims] = useState(null);

  // Viewport Pan, Zoom & Comparison
  const [zoomLevel, setZoomLevel] = useState(1.0);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });

  const canvasRef = useRef(null);
  const stageRef = useRef(null);
  const drawnChunksRef = useRef(new Set());
  const cellsRef = useRef(new Map());          // cell_index -> matched cell, buffered while processing
  const cellGridRef = useRef(new Map());       // "col,row" -> cell, for click-to-inspect
  const jobDimsRef = useRef(null);             // target dimensions of the current photomosaic job
  const imageCacheRef = useRef(new Map());     // tile url -> Promise<HTMLImageElement | null>
  const assemblyRef = useRef(null);            // { token, done: Promise } for the running fly-in
  const mountRef = useRef(null);
  const pointerDownRef = useRef(null);
  const targetImageRef = useRef(null);
  const liveScaleRef = useRef(1);
  const finalBaseImageRef = useRef(null); // untinted artboard preview; tint is composited live
  const vaultPollRef = useRef(null);
  const fileInputRef = useRef(null);
  const vaultFileInputRef = useRef(null);
  const vaultFolderInputRef = useRef(null);
  const pollIntervalRef = useRef(null);

  const tagsList = ['Landscapes', 'Architecture', 'Portraits', 'Textures', 'Vehicles', 'Nature', 'Cityscapes'];

  // Load Community Stats & Personal Vault Photos on Mount
  const loadCommunityStats = useCallback(async (tags) => {
    try {
      const query = tags && tags.length ? `?tags=${encodeURIComponent(tags.join(','))}` : '';
      const res = await fetch(`${GATEWAY_URL}/api/v1/library/community/stats${query}`);
      if (res.ok) {
        const data = await res.json();
        setCommunityCount(data.total_photos || 0);
        setCommunityFilteredCount(data.filtered_photos ?? data.total_photos ?? 0);
        setCommunityTagCounts(data.tag_counts || {});
      }
    } catch (e) {
      console.warn('Failed to load community stats:', e);
    }
  }, []);

  const loadPersonalPhotos = useCallback(async (currentVaultId) => {
    if (!currentVaultId) {
      setPersonalPhotos([]);
      setVaultPending(0);
      return null;
    }
    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/library/personal`, { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        setPersonalPhotos(data.photos || []);
        setVaultPending(data.pending_count || 0);
        return data;
      }
    } catch (e) {
      console.warn('Failed to load personal photos:', e);
    }
    return null;
  }, []);

  useEffect(() => {
    loadPersonalPhotos(vaultKey);
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      if (vaultPollRef.current) clearInterval(vaultPollRef.current);
    };
  }, [vaultKey, loadPersonalPhotos]);

  // Guests: leaving the site discards their photos, so the browser asks before closing the tab
  useEffect(() => {
    if (!isGuest || personalPhotos.length === 0) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [isGuest, personalPhotos.length]);

  // Tag filters narrow the community pool, so the available-tile count follows them.
  useEffect(() => {
    loadCommunityStats(selectedTags);
  }, [selectedTags, loadCommunityStats]);

  // After a direct upload, workers analyse the photos asynchronously; follow until the queue drains.
  const followVaultIngestion = (countBefore, uploadedCount) => {
    if (vaultPollRef.current) clearInterval(vaultPollRef.current);
    const startedAt = Date.now();
    vaultPollRef.current = setInterval(async () => {
      const data = await loadPersonalPhotos(vaultKey);
      const pending = data?.pending_count ?? 0;
      if (data && pending === 0) {
        clearInterval(vaultPollRef.current);
        vaultPollRef.current = null;
        const added = Math.max(0, (data.count || 0) - countBefore);
        const rejected = Math.max(0, uploadedCount - added);
        setVaultNotice(rejected > 0
          ? { type: 'error', text: `${added} photos added to your vault; ${rejected} could not be read as images and were skipped.` }
          : { type: 'success', text: `${added} photos uploaded, analysed and added to your vault.` });
      } else if (Date.now() - startedAt > 180000) {
        clearInterval(vaultPollRef.current);
        vaultPollRef.current = null;
        setVaultNotice({ type: 'error', text: `${pending} photos are still waiting for a worker to analyse them. Check that workers are running.` });
      }
    }, 1000);
  };

  // Personal Vault: direct browser-to-bucket upload (BRD 4.1) with live progress
  const handleUploadPersonalPhotos = async (files, inputEl) => {
    if (!files || files.length === 0) return;
    const validFiles = Array.from(files).filter(isImageFile);
    // Reset the input so selecting the same files again (e.g. after Clear Vault) fires onChange
    if (inputEl) inputEl.value = '';
    if (validFiles.length === 0) {
      setVaultNotice({ type: 'error', text: 'Please select valid photographic images (JPEG, PNG, WebP).' });
      return;
    }

    setVaultNotice(null);
    setConfirmingClear(false);
    setIsVaultUploading(true);
    setVaultUploadProgress(0);
    setVaultUploadStatus(`Uploading 0 / ${validFiles.length} photos to cloud storage...`);
    const countBefore = personalPhotos.length;

    try {
      const { uploaded, failed } = await directUpload(
        validFiles,
        { pool: 'personal', tags: uploadTags },
        (done, total) => {
          const pct = Math.round((done / total) * 100);
          setVaultUploadProgress(pct);
          setVaultUploadStatus(`Uploading ${done} / ${total} photos (${pct}%)...`);
        }
      );
      if (failed.length) {
        setVaultNotice({ type: 'error', text: `${failed.length} files failed to upload: ${failed.slice(0, 3).join(', ')}${failed.length > 3 ? '…' : ''}` });
      }
      if (uploaded > 0) followVaultIngestion(countBefore, uploaded);
    } catch (err) {
      setVaultNotice({ type: 'error', text: `Vault upload failed: ${err.message}` });
    } finally {
      setIsVaultUploading(false);
      setVaultUploadStatus('');
      setVaultUploadProgress(0);
      loadPersonalPhotos(vaultKey);
    }
  };

  const handleVaultDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsVaultDragging(false);
    if (!isVaultUploading && e.dataTransfer.files?.length) handleUploadPersonalPhotos(e.dataTransfer.files);
  };

  // Personal Vault: Clear / Purge (two-step inline confirmation)
  const handleClearVault = async () => {
    if (vaultPollRef.current) { clearInterval(vaultPollRef.current); vaultPollRef.current = null; }
    setIsClearingVault(true);
    setVaultNotice(null);
    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/library/personal`, {
        method: 'DELETE',
        credentials: 'same-origin',
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setPersonalPhotos([]);
      setVaultNotice({ type: 'success', text: `Vault cleared: ${data.deleted_photos ?? 0} photos permanently removed.` });
    } catch (err) {
      setVaultNotice({ type: 'error', text: `Failed to clear vault: ${err.message}` });
    } finally {
      setIsClearingVault(false);
      setConfirmingClear(false);
      // Re-sync with storage so the tray always reflects what is actually in the bucket
      loadPersonalPhotos(vaultKey);
    }
  };

  const toggleTag = (tag) => {
    if (selectedTags.includes(tag)) {
      setSelectedTags(selectedTags.filter((t) => t !== tag));
    } else {
      setSelectedTags([...selectedTags, tag]);
    }
  };

  const processFile = (file) => {
    if (!file || !file.type.startsWith('image/')) {
      setJobNotice({ type: 'error', text: 'Please provide a valid image (JPEG, PNG, WebP).' });
      return;
    }
    setJobNotice(null);
    setTargetFile(file);
    const url = URL.createObjectURL(file);
    setTargetPreviewUrl(url);
    setJobCompleted(false);
    setFinalMosaicUrl(null);
    finalBaseImageRef.current = null;

    const img = new Image();
    img.src = url;
    img.onload = () => {
      targetImageRef.current = img;
      setImageMeta({
        name: file.name,
        width: img.width,
        height: img.height,
        sizeMb: (file.size / (1024 * 1024)).toFixed(2),
      });
      renderToCanvas(img);
    };
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) processFile(file);
  };

  // Canvas Renderer & ASCII Aspect Ratio Fix (Section 1.2)
  const renderToCanvas = (imageObj) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const img = imageObj || new Image();
    if (!imageObj) img.src = targetPreviewUrl;

    const render = () => {
      const imgW = img.width || 800;
      const imgH = img.height || 600;

      if (selectedMode === 'ascii') {
        // EXACT ASPECT RATIO CALCULATION (Section 1.2 Bug Fix)
        // C = target_columns, R = round(C * (H / W) * (char_w / char_h))
        const charW = 8;
        const charH = 14;
        const C = Math.max(30, gridDensity);
        const R = Math.max(10, Math.round(C * (imgH / imgW) * (charW / charH)));

        // Buffer dimensions match exact output pixel dimensions: zero black strip borders!
        canvas.width = C * charW;
        canvas.height = R * charH;

        renderAsciiArtExact(ctx, img, C, R, charW, charH);
      } else {
        // Standard high-resolution master viewport
        const maxDisplayW = 960;
        canvas.width = maxDisplayW;
        canvas.height = Math.round(maxDisplayW * (imgH / imgW));

        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

        // Dynamic Alpha-Tinting Blend (Task 4.2)
        if (alphaTint > 0) {
          const alpha = Math.min(0.5, alphaTint / 100.0);
          ctx.globalAlpha = alpha;
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          ctx.globalAlpha = 1.0;
        }
      }
    };

    if (imageObj) render();
    else img.onload = render;
  };

  // Exact ASCII Renderer without Black Strips (Section 1.2)
  // Preview of the worker's ASCII render: equalised luminance picks the density glyph, the
  // strongest Sobel contours become line glyphs, and True Color paints each glyph in its cell's
  // source colour. (The worker uses CLAHE; the preview uses a global equalisation, which is close.)
  const renderAsciiArtExact = (ctx, img, C, R, charW, charH) => {
    const trueColor = asciiTheme === 'truecolor';
    let bg = '#07090e';
    let fg = '#38bdf8';
    if (asciiTheme === 'green') { bg = '#040d06'; fg = '#10b981'; }
    else if (asciiTheme === 'amber') { bg = '#120d03'; fg = '#f59e0b'; }
    else if (asciiTheme === 'mono') { bg = '#000000'; fg = '#f8fafc'; }
    else if (trueColor) { bg = '#06070a'; }

    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, C * charW, R * charH);

    // Offscreen downscale: one pixel per character cell
    const off = document.createElement('canvas');
    off.width = C;
    off.height = R;
    const offCtx = off.getContext('2d');
    offCtx.imageSmoothingQuality = 'high';
    offCtx.drawImage(img, 0, 0, C, R);
    const imgData = offCtx.getImageData(0, 0, C, R).data;

    const n = C * R;
    const lum = new Float32Array(n);
    const hist = new Uint32Array(256);
    for (let i = 0; i < n; i += 1) {
      const v = 0.299 * imgData[i * 4] + 0.587 * imgData[i * 4 + 1] + 0.114 * imgData[i * 4 + 2];
      lum[i] = v;
      hist[Math.round(v)] += 1;
    }
    // Histogram equalisation via the cumulative distribution
    const cdf = new Float32Array(256);
    let acc = 0;
    for (let v = 0; v < 256; v += 1) { acc += hist[v]; cdf[v] = acc / n; }
    const eq = (i) => cdf[Math.round(lum[i])];

    // 3x3 Sobel on the equalised grid; only the strongest ~18% of gradients become line glyphs
    const at = (x, y) => eq(Math.min(R - 1, Math.max(0, y)) * C + Math.min(C - 1, Math.max(0, x))) * 255;
    const mag = new Float32Array(n);
    const ang = new Float32Array(n);
    for (let y = 0; y < R; y += 1) {
      for (let x = 0; x < C; x += 1) {
        const gx = (at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x - 1, y) + at(x - 1, y + 1));
        const gy = (at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x, y - 1) + at(x + 1, y - 1));
        mag[y * C + x] = Math.hypot(gx, gy);
        ang[y * C + x] = ((Math.atan2(gy, gx) * 180) / Math.PI + 180) % 180;
      }
    }
    const sorted = Array.from(mag).sort((a, b) => a - b);
    const edgeCut = Math.max(35, sorted[Math.floor(sorted.length * 0.82)] || 0);

    const glyphs = asciiRamp === 'blocks'
      ? ' ░▒▓█'
      : asciiRamp === 'standard'
      ? ' .:-=+*#%@'
      : ' .`^",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$';

    ctx.font = `${charH - 2}px monospace`;
    ctx.textBaseline = 'top';
    if (!trueColor) ctx.fillStyle = fg;

    for (let r = 0; r < R; r++) {
      for (let c = 0; c < C; c++) {
        const i = r * C + c;
        let char;
        if (mag[i] > edgeCut) {
          const a = ang[i];
          char = a >= 67.5 && a < 112.5 ? '─' : a >= 22.5 && a < 67.5 ? '╱' : a >= 112.5 && a < 157.5 ? '╲' : '│';
        } else {
          const idx = Math.floor(eq(i) * (glyphs.length - 1));
          char = glyphs[Math.max(0, Math.min(glyphs.length - 1, idx))];
        }
        if (char === ' ') continue;
        if (trueColor) ctx.fillStyle = `rgb(${imgData[i * 4]}, ${imgData[i * 4 + 1]}, ${imgData[i * 4 + 2]})`;
        ctx.fillText(char, c * charW, r * charH);
      }
    }
  };

  useEffect(() => {
    if (targetPreviewUrl && !isProcessing && !finalMosaicUrl) {
      const img = new Image();
      img.src = targetPreviewUrl;
      img.onload = () => renderToCanvas(img);
    }
  }, [alphaTint, selectedMode, asciiTheme, asciiRamp, gridDensity, finalMosaicUrl, isProcessing]);

  // Pool Validation (Section 2.3)
  // BRD 4.3: with tags selected, only photos carrying one of them are eligible (both pools)
  const personalFilteredCount = selectedTags.length === 0
    ? personalPhotos.length
    : personalPhotos.filter((p) => (p.tags || []).some((t) => selectedTags.includes(t))).length;
  const personalActive = usePersonal ? personalFilteredCount : 0;
  const communityActive = useCommunityPhotos ? communityFilteredCount : 0;
  const showBlendSlider = personalActive > 0 && communityActive > 0;
  const totalSelectedPoolPhotos = personalActive + communityActive;
  const isPoolConfigValid = (usePersonal || useCommunityPhotos) && totalSelectedPoolPhotos > 0;

  // Rows follow the target's aspect ratio (the gateway derives them the same way)
  const gridRows = imageMeta
    ? Math.max(1, Math.round(gridDensity * (imageMeta.height / imageMeta.width)))
    : gridDensity;

  // Dispatch Job to Gateway & Polling Loop (Section 1.1)
  const handleSubmitJob = async () => {
    if (!targetFile) {
      setJobNotice({ type: 'error', text: 'Please upload a master target image first.' });
      return;
    }
    if (selectedMode === 'photomosaic' && !isPoolConfigValid) {
      setJobNotice({ type: 'error', text: 'Select at least one photo pool that contains photos.' });
      return;
    }

    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);

    setJobNotice(null);
    setIsProcessing(true);
    setJobProgress(0);
    setCompletedChunks(0);
    setTotalChunks(1);
    setJobCompleted(false);
    setFinalMosaicUrl(null);
    setCanvasReady(false);
    setAssemblyPhase(null);
    setDetailTiles([]);
    setInspect(null);
    drawnChunksRef.current = new Set();
    cellsRef.current = new Map();
    cellGridRef.current = new Map();
    jobDimsRef.current = null;
    assemblyRef.current = null;
    liveScaleRef.current = 0; // force the ghosted underlay to be redrawn for this job
    finalBaseImageRef.current = null;
    setExportState(null);
    setLastJobDims(null);
    setStatusMessage('Uploading & Partitioning Target into Cloud Chunks...');

    const formData = new FormData();
    formData.append('target_file', targetFile);
    formData.append(
      'config_json',
      JSON.stringify({
        mode: selectedMode,
        columns: gridDensity,
        rows: gridDensity,
        export_resolution: exportResolution,
        metric: 'CIELAB_DELTA_E_2000',
        use_personal_photos: usePersonal,
        use_community_photos: useCommunityPhotos,
        selected_tags: selectedTags,
        no_repeat_radius: noRepeatRadius,
        alpha_tint_percentage: alphaTint,
        ascii_theme: asciiTheme,
        kuwahara_radius: kuwaharaRadius,
        step_length: stepLength,
        num_particles: numParticles,
        stroke_width: strokeWidth,
        ascii_ramp: asciiRamp,
        blend_ratio_personal: blendPersonalPct / 100,
      })
    );

    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/jobs/create`, {
        method: 'POST',
        credentials: GATEWAY_URL ? 'include' : 'same-origin',
        body: formData,
      });

      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      const currentJobId = data.job_id;
      setJobId(currentJobId);
      setTotalChunks(data.total_chunks || 1);
      setStatusMessage('Distributed workers processing...');

      // 1-Second Interval Active Polling Loop (Section 1.1)
      startJobStatusPolling(currentJobId, data.total_chunks || 1, selectedMode);
    } catch (err) {
      console.error('Job creation error:', err);
      setJobNotice({ type: 'error', text: `Job dispatch failed: ${err.message}` });
      setIsProcessing(false);
    }
  };

  // Sizes the canvas for live tile reveal (display scale) and lays down a faint target underlay.
  const prepareLiveCanvas = (canvas, targetW, targetH) => {
    const scale = Math.min(1, LIVE_CANVAS_MAX_EDGE / Math.max(targetW, targetH));
    const w = Math.round(targetW * scale);
    const h = Math.round(targetH * scale);
    if (canvas.width === w && canvas.height === h && liveScaleRef.current === scale) return scale;
    liveScaleRef.current = scale;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#0a0c12';
    ctx.fillRect(0, 0, w, h);
    if (targetImageRef.current) {
      ctx.globalAlpha = 0.35;
      ctx.drawImage(targetImageRef.current, 0, 0, w, h);
      ctx.globalAlpha = 1.0;
    }
    return scale;
  };

  // BRD 4.5: (1 - a) * Mosaic + a * Target, recomposited whenever the tint slider moves.
  const drawFinalArtboard = useCallback((tintPct) => {
    const base = finalBaseImageRef.current;
    const canvas = canvasRef.current;
    if (!base || !canvas) return;
    const ctx = canvas.getContext('2d');
    if (canvas.width !== base.width || canvas.height !== base.height) {
      canvas.width = base.width;
      canvas.height = base.height;
    }
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.globalAlpha = 1.0;
    ctx.drawImage(base, 0, 0);
    const alpha = Math.min(0.5, Math.max(0, tintPct) / 100);
    if (alpha > 0 && targetImageRef.current) {
      ctx.globalAlpha = alpha;
      ctx.drawImage(targetImageRef.current, 0, 0, base.width, base.height);
      ctx.globalAlpha = 1.0;
    }
  }, []);

  const showFinalArtboard = (id) => {
    const finalUrl = `${GATEWAY_URL}/api/v1/jobs/${id}/result`;
    setFinalMosaicUrl(finalUrl);

    // Display the untinted preview-sized JPEG and composite the tint client-side; the
    // full-resolution PNG (with the chosen tint) is generated only on export.
    const masterImg = new Image();
    masterImg.crossOrigin = 'anonymous';
    masterImg.onload = () => {
      finalBaseImageRef.current = masterImg;
      setCanvasReady(true);
      drawFinalArtboard(alphaTintRef.current);
      setPanOffset({ x: 0, y: 0 });
      setZoomLevel(1.0);
    };
    masterImg.onerror = () => {
      setJobNotice({ type: 'error', text: 'The artboard finished but its preview could not be loaded. Try Export to download it.' });
    };
    masterImg.src = `${GATEWAY_URL}/api/v1/jobs/${id}/preview?tint=0&t=${Date.now()}`;
  };

  // Polling closures see stale state; keep the latest tint in a ref.
  const alphaTintRef = useRef(alphaTint);
  useEffect(() => {
    alphaTintRef.current = alphaTint;
    if (finalBaseImageRef.current && !isProcessing) drawFinalArtboard(alphaTint);
  }, [alphaTint, isProcessing, drawFinalArtboard]);

  const loadImage = (url) => {
    const cache = imageCacheRef.current;
    if (!cache.has(url)) {
      cache.set(url, new Promise((resolve) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => resolve(img);
        img.onerror = () => resolve(null);
        img.src = `${GATEWAY_URL}${url}`;
      }));
    }
    return cache.get(url);
  };

  // Phase 1: finished chunks are buffered (and their thumbnails preloaded), not painted, so the
  // canvas keeps showing the ghosted target instead of filling in like a printer scanline.
  const bufferPatches = async (id) => {
    const have = Array.from(drawnChunksRef.current).join(',');
    const res = await fetch(`${GATEWAY_URL}/api/v1/jobs/${id}/progress-patches?have=${have}`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.target_dimensions) {
      jobDimsRef.current = data.target_dimensions;
      const canvas = canvasRef.current;
      if (canvas) prepareLiveCanvas(canvas, data.target_dimensions.width, data.target_dimensions.height);
    }
    (data.patches || []).forEach((patch) => {
      if (drawnChunksRef.current.has(patch.chunk_index)) return;
      drawnChunksRef.current.add(patch.chunk_index);
      (patch.cells || []).forEach((cell) => {
        const key = cell.cell_index ?? `${cell.col},${cell.row}`;
        cellsRef.current.set(key, cell);
        cellGridRef.current.set(`${cell.col},${cell.row}`, cell);
        if (cell.thumb) loadImage(cell.thumb);
      });
    });
  };

  // Phase 2: every tile flies in from a random edge with a slight rotation, staggered over
  // 1.5-2.5 s on cubic-bezier(0.16, 1, 0.3, 1), and docks with a glow ripple.
  const runFlyIn = async (token) => {
    const canvas = canvasRef.current;
    const dims = jobDimsRef.current;
    if (!canvas || !dims) return;
    const scale = prepareLiveCanvas(canvas, dims.width, dims.height);
    const ctx = canvas.getContext('2d');
    const cells = Array.from(cellsRef.current.values());
    const urls = [...new Set(cells.map((c) => c.thumb).filter(Boolean))];
    const loaded = await Promise.race([
      Promise.all(urls.map((u) => loadImage(u).then((img) => [u, img]))),
      new Promise((r) => setTimeout(() => r(null), 6000)),
    ]);
    const images = new Map(loaded || []);
    if (assemblyRef.current?.token !== token) return;

    const W = canvas.width, H = canvas.height;
    const docked = document.createElement('canvas');
    docked.width = W;
    docked.height = H;
    const dctx = docked.getContext('2d');
    dctx.drawImage(canvas, 0, 0); // ghosted target underlay
    const paint = (c2d, t, x, y) => {
      if (t.img) c2d.drawImage(t.img, x, y, t.w, t.h);
      else { c2d.fillStyle = t.color; c2d.fillRect(x, y, t.w, t.h); }
    };

    const n = cells.length;
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const tiles = cells.map((c) => ({
      x: c.px * scale, y: c.py * scale, w: c.w * scale, h: c.h * scale,
      img: c.thumb ? images.get(c.thumb) : null,
      color: `rgb(${c.rgb[0]}, ${c.rgb[1]}, ${c.rgb[2]})`,
    }));
    if (reduceMotion || n === 0) {
      tiles.forEach((t) => paint(ctx, t, t.x, t.y));
      return;
    }

    const total = Math.min(2.5, Math.max(1.5, 1.5 + n / 6000));
    const spread = total - FLY_DURATION;
    const rippleEvery = n > 6000 ? 4 : 1; // keep very large grids smooth
    tiles.forEach((t, i) => {
      t.delay = Math.random() * spread;
      const side = Math.floor(Math.random() * 4);
      const jitterX = (Math.random() - 0.5) * W * 0.5;
      const jitterY = (Math.random() - 0.5) * H * 0.5;
      if (side === 0) { t.x0 = -t.w * 1.5 - Math.random() * W * 0.2; t.y0 = t.y + jitterY; }
      else if (side === 1) { t.x0 = W + t.w * 0.5 + Math.random() * W * 0.2; t.y0 = t.y + jitterY; }
      else if (side === 2) { t.x0 = t.x + jitterX; t.y0 = -t.h * 1.5 - Math.random() * H * 0.2; }
      else { t.x0 = t.x + jitterX; t.y0 = H + t.h * 0.5 + Math.random() * H * 0.2; }
      t.rot0 = ((Math.random() * 40) - 20) * (Math.PI / 180);
      t.ripple = i % rippleEvery === 0;
    });
    tiles.sort((a, b) => a.delay - b.delay);

    await new Promise((resolve) => {
      const start = performance.now();
      let next = 0;
      let active = [];
      let ripples = [];
      const frame = (now) => {
        if (assemblyRef.current?.token !== token) { resolve(); return; }
        const e = (now - start) / 1000;
        while (next < tiles.length && tiles[next].delay <= e) active.push(tiles[next++]);
        ctx.drawImage(docked, 0, 0);
        const flying = [];
        for (const t of active) {
          const p = (e - t.delay) / FLY_DURATION;
          if (p >= 1) {
            paint(dctx, t, t.x, t.y);
            paint(ctx, t, t.x, t.y);
            if (t.ripple) ripples.push({ t, at: e });
            continue;
          }
          flying.push(t);
          const k = easeFlyIn(p);
          const x = t.x0 + (t.x - t.x0) * k;
          const y = t.y0 + (t.y - t.y0) * k;
          ctx.save();
          ctx.globalAlpha = Math.min(1, p * 4);
          ctx.translate(x + t.w / 2, y + t.h / 2);
          ctx.rotate(t.rot0 * (1 - k));
          paint(ctx, t, -t.w / 2, -t.h / 2);
          ctx.restore();
        }
        active = flying;
        ripples = ripples.filter((r) => e - r.at < RIPPLE_DURATION);
        for (const r of ripples) {
          const q = (e - r.at) / RIPPLE_DURATION;
          const grow = r.t.w * 0.35 * q;
          ctx.strokeStyle = `rgba(0, 229, 255, ${0.85 * (1 - q)})`;
          ctx.lineWidth = Math.max(1, r.t.w * 0.12 * (1 - q));
          ctx.strokeRect(r.t.x - grow, r.t.y - grow, r.t.w + 2 * grow, r.t.h + 2 * grow);
        }
        if (next >= tiles.length && active.length === 0 && ripples.length === 0) {
          ctx.drawImage(docked, 0, 0);
          resolve();
          return;
        }
        requestAnimationFrame(frame);
      };
      requestAnimationFrame(frame);
    });
  };

  const startAssembly = (id) => {
    if (assemblyRef.current) return assemblyRef.current.done;
    const token = Symbol(id);
    setAssemblyPhase('assembling');
    setStatusMessage('Assembling tiles…');
    assemblyRef.current = { token, done: null };
    assemblyRef.current.done = runFlyIn(token).catch((e) => console.warn('Fly-in failed:', e));
    return assemblyRef.current.done;
  };

  const startJobStatusPolling = (id, expectedTotalChunks, mode) => {
    let inFlight = false;
    pollIntervalRef.current = setInterval(async () => {
      if (inFlight) return; // never stack overlapping polls on a slow network
      inFlight = true;
      try {
        const res = await fetch(`${GATEWAY_URL}/api/v1/jobs/${id}/status`);
        if (!res.ok) return;

        const status = await res.json();
        const total = status.total_chunks || expectedTotalChunks || 1;
        const completed = status.completed_chunks || 0;

        // Dynamic formula: (completed_chunks / total_chunks) * 100; held at 99% while stitching
        const computedProgress = status.is_complete
          ? 100
          : Math.min(99, Math.round((completed / Math.max(1, total)) * 100));

        setCompletedChunks(completed);
        setTotalChunks(total);
        setJobProgress(computedProgress);
        if (!assemblyRef.current) setStatusMessage(status.status_text || 'Distributed workers processing...');

        if (status.is_failed) {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
          assemblyRef.current = null;
          setAssemblyPhase(null);
          setIsProcessing(false);
          setJobNotice({ type: 'error', text: status.status_text || 'The job failed during assembly.' });
          return;
        }

        if (mode === 'photomosaic') {
          // Ghosted target poster from the first poll, before any chunk has finished
          if (!jobDimsRef.current && status.target_dimensions && canvasRef.current) {
            jobDimsRef.current = status.target_dimensions;
            prepareLiveCanvas(canvasRef.current, status.target_dimensions.width, status.target_dimensions.height);
          }
          if (drawnChunksRef.current.size < total) {
            try {
              await bufferPatches(id);
            } catch (patchErr) {
              console.warn('Patch poll exception:', patchErr);
            }
          }
          // Every chunk processed: fly the tiles in while the assembler stitches the full-res artboard
          if (drawnChunksRef.current.size >= total) startAssembly(id);
        }

        // Job Completion: reveal the stitched artboard once the fly-in has docked every tile
        if (status.is_complete) {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
          if (assemblyRef.current) await assemblyRef.current.done;
          setAssemblyPhase(null);
          setIsProcessing(false);
          setJobCompleted(true);
          setStatusMessage('Master Artboard Ready');
          setLastJobDims(mode === 'photomosaic' ? status.target_dimensions : null);
          showFinalArtboard(id);
        }
      } catch (e) {
        console.warn('Status poll exception:', e);
      } finally {
        inFlight = false;
      }
    }, 1000); // 1-second interval requirement (Section 1.1)
  };

  // High-res detail layer: above 200% zoom, the tiles in view are overlaid from their 256px
  // originals so they stay sharp instead of magnifying the 2400px preview.
  const inspectable = jobCompleted && Boolean(lastJobDims) && cellsRef.current.size > 0;
  useEffect(() => {
    if (!inspectable || zoomLevel <= DETAIL_ZOOM) {
      setDetailTiles((prev) => (prev.length ? [] : prev));
      return undefined;
    }
    const timer = setTimeout(() => {
      const mount = mountRef.current;
      const stage = stageRef.current;
      const dims = jobDimsRef.current;
      if (!mount || !stage || !dims) return;
      const mr = mount.getBoundingClientRect();
      const sr = stage.getBoundingClientRect();
      const visible = [];
      for (const c of cellsRef.current.values()) {
        if (!c.thumb) continue;
        const left = mr.left + (c.px / dims.width) * mr.width;
        const top = mr.top + (c.py / dims.height) * mr.height;
        const right = left + (c.w / dims.width) * mr.width;
        const bottom = top + (c.h / dims.height) * mr.height;
        if (right > sr.left && left < sr.right && bottom > sr.top && top < sr.bottom) {
          visible.push(c);
          if (visible.length >= DETAIL_MAX_TILES) break;
        }
      }
      setDetailTiles(visible);
    }, 120);
    return () => clearTimeout(timer);
  }, [inspectable, zoomLevel, panOffset]);

  // Tile inspector: the full-resolution original, its contributor and the ΔE00 match score
  const openInspector = async (cell) => {
    if (cell.cell_index === undefined || cell.cell_index === null) return;
    setInspect({ cell, loading: true });
    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/jobs/${jobId}/cells/${cell.cell_index}`);
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setInspect({ cell, loading: false, data });
    } catch (err) {
      setInspect({ cell, loading: false, error: err.message });
    }
  };

  useEffect(() => {
    if (!inspect) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setInspect(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [inspect]);

  const cellAtPoint = (clientX, clientY) => {
    const mount = mountRef.current;
    const dims = jobDimsRef.current;
    const first = cellsRef.current.values().next().value;
    if (!mount || !dims || !first) return null;
    const mr = mount.getBoundingClientRect();
    const fx = ((clientX - mr.left) / mr.width) * dims.width;
    const fy = ((clientY - mr.top) / mr.height) * dims.height;
    if (fx < 0 || fy < 0 || fx >= dims.width || fy >= dims.height) return null;
    return cellGridRef.current.get(`${Math.floor(fx / first.w)},${Math.floor(fy / first.h)}`) || null;
  };

  // Pan & Zoom Controls
  const handleMouseDown = (e) => {
    pointerDownRef.current = { x: e.clientX, y: e.clientY };
    setIsPanning(true);
    setPanStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
  };

  const handleMouseMove = (e) => {
    if (!isPanning) return;
    setPanOffset({ x: e.clientX - panStart.x, y: e.clientY - panStart.y });
  };

  const handleMouseUp = (e) => {
    setIsPanning(false);
    const down = pointerDownRef.current;
    pointerDownRef.current = null;
    if (!inspectable || !down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
    const cell = cellAtPoint(e.clientX, e.clientY);
    if (cell) openInspector(cell);
  };

  const handleWheel = (e) => {
    e.preventDefault();
    const step = e.deltaY > 0 ? -0.1 : 0.1;
    setZoomLevel((z) => Math.max(0.3, Math.min(3.5, z + step)));
  };

  const resetViewport = () => {
    setZoomLevel(1.0);
    setPanOffset({ x: 0, y: 0 });
  };

  const exportUrl = (fmt, download) =>
    `${GATEWAY_URL}/api/v1/jobs/${jobId}/result?tint=${alphaTint}&format=${fmt}${download ? '&download=true' : ''}`;

  // Full-resolution export. The server renders the tinted artboard (8k-12k PNGs take several
  // seconds), so fetch it with visible progress, then save it from a same-origin blob URL so the
  // filename is kept and the save cannot be dropped. The blob URL stays alive long enough for
  // the browser to finish writing the file (revoking it immediately makes downloads fail).
  const exportArtboard = async () => {
    if (!jobCompleted || !jobId || exportState?.busy) return;
    const fmt = exportFormat;
    const dims = lastJobDims ? `${lastJobDims.width} × ${lastJobDims.height}` : 'full-resolution';
    setExportState({ busy: true, text: `Rendering ${dims} ${fmt.toUpperCase()}…` });
    try {
      const res = await fetch(exportUrl(fmt, false));
      if (!res.ok) throw new Error(await readError(res));
      const total = Number(res.headers.get('Content-Length')) || 0;
      const reader = res.body.getReader();
      const chunks = [];
      let received = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.length;
        const mb = (received / 1e6).toFixed(1);
        setExportState({
          busy: true,
          text: total ? `Downloading ${mb} / ${(total / 1e6).toFixed(1)} MB…` : `Downloading ${mb} MB…`,
          pct: total ? Math.round((received / total) * 100) : null,
        });
      }
      const blob = new Blob(chunks, { type: fmt === 'jpg' ? 'image/jpeg' : 'image/png' });
      const filename = `pixelmosaic_${jobId}_tint${alphaTint}.${fmt}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 120000);
      setExportState({
        busy: false,
        done: true,
        text: `Saved ${filename} (${dims} px, ${(blob.size / 1e6).toFixed(1)} MB) to your Downloads folder.`,
      });
    } catch (err) {
      setExportState({ busy: false, error: true, text: `Export failed: ${err.message}` });
    }
  };

  return (
    <div className="studio-frame">
      {/* LEFT INSPECTOR SIDEBAR */}
      <div className="inspector-sidebar">
        {/* Master Poster Upload Card */}
        <div className="inspector-card">
          <div className="inspector-card-header">
            <span className="inspector-card-title">Target Master Image</span>
            {imageMeta && <span className="param-val-bubble">{imageMeta.sizeMb} MB</span>}
          </div>

          <div
            className={`file-drop-zone ${isDragging ? 'active' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              type="file"
              ref={fileInputRef}
              style={{ display: 'none' }}
              accept="image/*"
              onChange={(e) => e.target.files?.[0] && processFile(e.target.files[0])}
            />

            {imageMeta ? (
              <div className="file-preview-strip">
                <img src={targetPreviewUrl} alt="Target" className="preview-thumbnail" />
                <div className="preview-details">
                  <div className="preview-filename">{imageMeta.name}</div>
                  <div className="preview-meta">{imageMeta.width} &times; {imageMeta.height} px &bull; Ready</div>
                </div>
              </div>
            ) : (
              <div>
                <IconUpload size={22} className="card-icon" style={{ margin: '0 auto 0.4rem', color: 'var(--text-tertiary)' }} />
                <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#fff', marginBottom: '0.2rem' }}>
                  Choose Image or Drag &amp; Drop
                </div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                  PNG, JPEG, WebP up to 30 MB
                </div>
              </div>
            )}
          </div>
        </div>

        {/* SECTION 2.2: PERSONAL PHOTO VAULT (drop zone: BRD 4.1 drag & drop ingestion) */}
        <div
          className="inspector-card"
          style={{
            borderColor: isVaultDragging ? 'var(--accent-cyan)' : 'rgba(0, 229, 255, 0.25)',
            background: isVaultDragging ? 'rgba(0, 229, 255, 0.06)' : undefined,
            transition: 'border-color 0.15s ease, background 0.15s ease'
          }}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setIsVaultDragging(true); }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setIsVaultDragging(false); }}
          onDrop={handleVaultDrop}
        >
          <div className="inspector-card-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <span className="inspector-card-title">Personal Photo Vault</span>
              <span style={{
                background: 'rgba(0, 229, 255, 0.15)',
                color: 'var(--accent-cyan)',
                fontSize: '0.68rem',
                fontFamily: 'var(--font-mono)',
                padding: '0.15rem 0.45rem',
                borderRadius: '4px',
                fontWeight: 700
              }}>
                {personalPhotos.length} Photos
              </span>
            </div>

            {personalPhotos.length > 0 && !confirmingClear && (
              <button
                type="button"
                onClick={() => { setConfirmingClear(true); setVaultNotice(null); }}
                disabled={isVaultUploading}
                style={{
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--accent-pink)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.25rem',
                  fontSize: '0.7rem',
                  fontWeight: 700
                }}
                title="Clear and purge all photos from vault"
              >
                <IconTrash size={13} />
                Clear Vault
              </button>
            )}
          </div>

          {confirmingClear && (
            <div
              role="alertdialog"
              aria-label="Confirm clearing the vault"
              style={{
                background: 'rgba(255, 42, 133, 0.08)',
                border: '1px solid rgba(255, 42, 133, 0.4)',
                borderRadius: '8px',
                padding: '0.65rem 0.75rem',
                marginBottom: '0.75rem',
                fontSize: '0.74rem',
                color: 'var(--text-primary)'
              }}
            >
              <div style={{ marginBottom: '0.55rem', lineHeight: 1.4 }}>
                Permanently delete all <strong>{personalPhotos.length}</strong> photos from your vault? This cannot be undone.
              </div>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ flex: 1, padding: '0.4rem', fontSize: '0.72rem' }}
                  onClick={() => setConfirmingClear(false)}
                  disabled={isClearingVault}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{
                    flex: 1,
                    padding: '0.4rem',
                    fontSize: '0.72rem',
                    fontWeight: 700,
                    background: 'var(--accent-pink)',
                    color: '#fff',
                    border: 'none',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '0.3rem'
                  }}
                  onClick={handleClearVault}
                  disabled={isClearingVault}
                >
                  <IconTrash size={12} />
                  {isClearingVault ? 'Deleting...' : 'Delete all photos'}
                </button>
              </div>
            </div>
          )}

          {isGuest && (
            <div className="guest-vault-warning" role="note">
              <strong>You're not signed in. Your photos won't be saved.</strong>
              <p>
                You can upload photos and make mosaics right away, but your vault only lasts for this visit:
                the photos are deleted when you leave the site or close the browser.
                Sign in or create an account to keep them; anything you've uploaded moves into your account.
              </p>
              <div className="guest-vault-actions">
                <button className="btn btn-secondary" onClick={() => onRequestSignIn?.('signup')}>Create account</button>
                <button className="btn btn-secondary" onClick={() => onRequestSignIn?.('login')}>Sign in</button>
              </div>
            </div>
          )}
          <>
          <Notice notice={vaultNotice} onDismiss={() => setVaultNotice(null)} />

          <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
            Drag &amp; drop photos here or use the buttons. Files upload straight to cloud storage and are analysed
            automatically.
          </div>

          <div style={{ marginBottom: '0.65rem' }}>
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginBottom: '0.3rem' }}>
              Tag new uploads (optional):
            </div>
            <div className="tag-chip-group">
              {tagsList.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  className={`tag-toggle-btn ${uploadTags.includes(tag) ? 'active' : ''}`}
                  style={{ fontSize: '0.66rem', padding: '0.2rem 0.5rem' }}
                  onClick={() => setUploadTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]))}
                >
                  {tag}
                </button>
              ))}
            </div>
          </div>

          {/* Action buttons: Add Photos / Add Folder */}
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.85rem' }}>
            <button
              className="btn btn-secondary"
              style={{ flex: 1, padding: '0.45rem 0.6rem', fontSize: '0.74rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.35rem' }}
              onClick={() => vaultFileInputRef.current?.click()}
              disabled={isVaultUploading || isClearingVault}
            >
              <IconUpload size={13} />
              + Add Photos
            </button>
            <button
              className="btn btn-secondary"
              style={{ flex: 1, padding: '0.45rem 0.6rem', fontSize: '0.74rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.35rem' }}
              onClick={() => vaultFolderInputRef.current?.click()}
              disabled={isVaultUploading || isClearingVault}
            >
              📁 + Add Folder
            </button>

            <input
              ref={vaultFileInputRef}
              type="file"
              multiple
              accept="image/*"
              style={{ display: 'none' }}
              onChange={(e) => handleUploadPersonalPhotos(e.target.files, e.target)}
            />
            <input
              ref={vaultFolderInputRef}
              type="file"
              multiple
              webkitdirectory="true"
              style={{ display: 'none' }}
              onChange={(e) => handleUploadPersonalPhotos(e.target.files, e.target)}
            />
          </div>

          {isVaultUploading && (
            <div style={{
              background: 'rgba(0, 229, 255, 0.06)',
              border: '1px solid rgba(0, 229, 255, 0.25)',
              borderRadius: '8px',
              padding: '0.65rem 0.85rem',
              marginBottom: '0.85rem'
            }}>
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                fontSize: '0.72rem',
                fontFamily: 'var(--font-mono)',
                color: 'var(--accent-cyan)',
                marginBottom: '0.4rem'
              }}>
                <span style={{ fontWeight: 600 }}>⚡ {vaultUploadStatus}</span>
                <span style={{ fontWeight: 800 }}>{vaultUploadProgress}%</span>
              </div>
              <div style={{
                width: '100%',
                height: '6px',
                background: 'rgba(255, 255, 255, 0.08)',
                borderRadius: '999px',
                overflow: 'hidden'
              }}>
                <div style={{
                  width: `${vaultUploadProgress}%`,
                  height: '100%',
                  background: 'linear-gradient(90deg, var(--accent-cyan), var(--accent-green))',
                  borderRadius: '999px',
                  transition: 'width 0.25s ease',
                  boxShadow: '0 0 10px rgba(0, 229, 255, 0.5)'
                }} />
              </div>
            </div>
          )}

          {vaultPending > 0 && (
            <div role="status" style={{ fontSize: '0.72rem', color: 'var(--accent-cyan)', marginBottom: '0.6rem', fontFamily: 'var(--font-mono)' }}>
              ⚙ Analysing {vaultPending} uploaded photo{vaultPending === 1 ? '' : 's'}…
            </div>
          )}

          {/* Persistent Thumbnail Tray */}
          {personalPhotos.length > 0 ? (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(5, 1fr)',
              gap: '0.35rem',
              maxHeight: '120px',
              overflowY: 'auto',
              padding: '0.4rem',
              background: 'rgba(0, 0, 0, 0.25)',
              borderRadius: '8px',
              border: '1px solid rgba(255, 255, 255, 0.05)'
            }}>
              {personalPhotos.map((p, idx) => (
                <div
                  key={p.id || idx}
                  style={{
                    aspectRatio: '1/1',
                    borderRadius: '4px',
                    overflow: 'hidden',
                    background: '#0c0e14',
                    border: '1px solid rgba(255, 255, 255, 0.08)'
                  }}
                  title={p.tags && p.tags.length ? `Tags: ${p.tags.join(', ')}` : 'Untagged'}
                >
                  <img
                    src={`${GATEWAY_URL}${p.thumb_url}`}
                    alt="Vault Tile"
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', textAlign: 'center', padding: '0.5rem', background: 'rgba(255,255,255,0.01)', borderRadius: '6px' }}>
              Vault is empty. Drop photos here or upload a folder.
            </div>
          )}
          </>
        </div>

        {/* 4 Engine Modes */}
        <div className="inspector-card">
          <div className="inspector-card-header">
            <span className="inspector-card-title">Raster Engine Mode</span>
          </div>
          <div className="engine-segmented-control">
            <button
              className={`engine-pill-btn ${selectedMode === 'photomosaic' ? 'active' : ''}`}
              onClick={() => setSelectedMode('photomosaic')}
            >
              <IconGrid size={16} />
              <span>Photomosaic</span>
            </button>
            <button
              className={`engine-pill-btn ${selectedMode === 'ascii' ? 'active' : ''}`}
              onClick={() => setSelectedMode('ascii')}
            >
              <IconAscii size={16} />
              <span>Sobel ASCII</span>
            </button>
            <button
              className={`engine-pill-btn ${selectedMode === 'kuwahara' ? 'active' : ''}`}
              onClick={() => setSelectedMode('kuwahara')}
            >
              <IconBrush size={16} />
              <span>Kuwahara Oil</span>
            </button>
            <button
              className={`engine-pill-btn ${selectedMode === 'flowfield' ? 'active' : ''}`}
              onClick={() => setSelectedMode('flowfield')}
            >
              <IconFlow size={16} />
              <span>Vector Flow Field</span>
            </button>
          </div>
        </div>

        {/* Mode-Specific Parameters */}
        {selectedMode === 'photomosaic' && (
          <div className="inspector-card">
            <div className="inspector-card-header">
              <span className="inspector-card-title">Photomosaic Settings</span>
            </div>

            {/* SECTION 2.3: POOL SOURCE SELECTION CHECKBOXES (Slider Removed) */}
            <div className="param-row" style={{ marginBottom: '1.25rem' }}>
              <div className="param-label-bar" style={{ marginBottom: '0.65rem' }}>
                <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>Asset Source Pool</span>
                <span className="param-val-bubble">{totalSelectedPoolPhotos} Available Tiles</span>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
                {/* Checkbox 1: Personal Photos */}
                <label style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.65rem',
                  background: usePersonal ? 'rgba(0, 229, 255, 0.08)' : 'rgba(255, 255, 255, 0.02)',
                  border: `1px solid ${usePersonal ? 'rgba(0, 229, 255, 0.4)' : 'rgba(255, 255, 255, 0.06)'}`,
                  padding: '0.65rem 0.85rem',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease'
                }}>
                  <input
                    type="checkbox"
                    checked={usePersonal}
                    onChange={(e) => setUsePersonalPhotos(e.target.checked)}
                    style={{ accentColor: 'var(--accent-cyan)', width: '16px', height: '16px', cursor: 'pointer' }}
                  />
                  <div style={{ flex: 1, fontSize: '0.78rem' }}>
                    <div style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                      Use Personal Photos
                    </div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                      {isGuest && personalPhotos.length > 0 && personalFilteredCount === personalPhotos.length
                        ? `Temporary vault: ${personalPhotos.length} photos (not saved when you leave)`
                        : personalFilteredCount === personalPhotos.length
                        ? `Vault: ${personalPhotos.length} photos loaded`
                        : `Vault: ${personalFilteredCount} of ${personalPhotos.length} photos match your tag filters`}
                    </div>
                  </div>
                </label>

                {/* Checkbox 2: Community Gallery */}
                <label style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.65rem',
                  background: useCommunityPhotos ? 'rgba(0, 255, 163, 0.08)' : 'rgba(255, 255, 255, 0.02)',
                  border: `1px solid ${useCommunityPhotos ? 'rgba(0, 255, 163, 0.4)' : 'rgba(255, 255, 255, 0.06)'}`,
                  padding: '0.65rem 0.85rem',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease'
                }}>
                  <input
                    type="checkbox"
                    checked={useCommunityPhotos}
                    onChange={(e) => setUseCommunityPhotos(e.target.checked)}
                    style={{ accentColor: 'var(--accent-green)', width: '16px', height: '16px', cursor: 'pointer' }}
                  />
                  <div style={{ flex: 1, fontSize: '0.78rem' }}>
                    <div style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                      Use Community Gallery
                    </div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                      {communityFilteredCount === communityCount
                        ? `Global Commons: ${communityCount} photos available`
                        : `Global Commons: ${communityFilteredCount} of ${communityCount} photos match your tag filters`}
                    </div>
                  </div>
                  {onNavigateToGallery && (
                    <button
                      type="button"
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); onNavigateToGallery(); }}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--accent-green)',
                        fontSize: '0.68rem',
                        cursor: 'pointer',
                        textDecoration: 'underline'
                      }}
                    >
                      Donate +
                    </button>
                  )}
                </label>
              </div>

              {/* BRD 4.2 Dual-Source Blend Ratio (only meaningful when both pools contribute) */}
              {showBlendSlider && (
                <div style={{ marginTop: '0.85rem' }}>
                  <div className="param-label-bar">
                    <span>Blend Ratio</span>
                    <span className="param-val-bubble">
                      {blendPersonalPct}% personal / {100 - blendPersonalPct}% community
                    </span>
                  </div>
                  <input
                    type="range"
                    className="custom-range"
                    min="0"
                    max="100"
                    step="5"
                    value={blendPersonalPct}
                    aria-label="Share of tiles taken from personal photos"
                    onChange={(e) => setBlendPersonalPct(Number(e.target.value))}
                  />
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.64rem', color: 'var(--text-muted)' }}>
                    <span>All community</span>
                    <span>All personal</span>
                  </div>
                </div>
              )}

              {/* Pool Validation Warning Banner (Section 2.3) */}
              {!isPoolConfigValid && (
                <div style={{
                  marginTop: '0.75rem',
                  background: 'rgba(255, 42, 133, 0.1)',
                  border: '1px solid rgba(255, 42, 133, 0.35)',
                  borderRadius: '8px',
                  padding: '0.65rem 0.85rem',
                  fontSize: '0.74rem',
                  color: 'var(--accent-pink)',
                  lineHeight: 1.4
                }}>
                  {selectedTags.length > 0 && (usePersonal || useCommunityPhotos) ? (
                    <>⚠️ <strong>No matching photos:</strong> none of the photos in the selected pools are tagged {selectedTags.join(', ')}. Change or clear the tag filters.</>
                  ) : (
                    <>⚠️ <strong>Photo Pool Incomplete:</strong> Select at least one active photo source with available photos before synthesizing.</>
                  )}
                </div>
              )}
            </div>

            <div className="param-row">
              <div className="param-label-bar">
                <span>Grid Resolution</span>
                <span className="param-val-bubble">{gridDensity} &times; {gridRows} ({gridDensity * gridRows} tiles)</span>
              </div>
              <input
                type="range"
                className="custom-range"
                min="25"
                max="120"
                step="5"
                value={gridDensity}
                onChange={(e) => setGridDensity(Number(e.target.value))}
              />
            </div>

            <div className="param-row">
              <div className="param-label-bar">
                <span>No-Repeat Proximity Radius</span>
                <span className="param-val-bubble">R = {noRepeatRadius} cells</span>
              </div>
              <input
                type="range"
                className="custom-range"
                min="0"
                max="15"
                value={noRepeatRadius}
                onChange={(e) => setNoRepeatRadius(Number(e.target.value))}
              />
              {noRepeatRadius > 1 && isPoolConfigValid && totalSelectedPoolPhotos < (2 * noRepeatRadius - 1) ** 2 && (
                <div style={{ fontSize: '0.68rem', color: 'var(--accent-amber, #f59e0b)', marginTop: '0.35rem', lineHeight: 1.4 }}>
                  R = {noRepeatRadius} needs at least {(2 * noRepeatRadius - 1) ** 2} distinct photos to be fully honoured; with
                  {' '}{totalSelectedPoolPhotos} the closest-colour tile is reused where needed. Add photos or lower R.
                </div>
              )}
            </div>

            <div className="param-row">
              <div className="param-label-bar">
                <span>Category Tag Filters</span>
                {selectedTags.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setSelectedTags([])}
                    style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', fontSize: '0.68rem', cursor: 'pointer', textDecoration: 'underline' }}
                  >
                    Clear filters
                  </button>
                )}
              </div>
              <div className="tag-chip-group">
                {tagsList.map((tag) => (
                  <button
                    key={tag}
                    className={`tag-toggle-btn ${selectedTags.includes(tag) ? 'active' : ''}`}
                    onClick={() => toggleTag(tag)}
                  >
                    {tag}
                    {communityTagCounts[tag] ? ` (${communityTagCounts[tag]})` : ''}
                  </button>
                ))}
              </div>
              <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '0.4rem', lineHeight: 1.4 }}>
                Only photos tagged with at least one selected category are used (vault and community).
                No selection = all photos are eligible.
              </div>
            </div>

            {/* Requirement 2: Master Canvas Export Resolution Selector */}
            <div className="param-row" style={{ marginTop: '1rem' }}>
              <div className="param-label-bar">
                <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>Master Canvas Export Resolution</span>
                <span className="param-val-bubble">
                  {imageMeta
                    ? (() => {
                        const tile = Math.max(50, Math.round(exportResolution / Math.max(gridDensity, gridRows)));
                        return `${gridDensity * tile} × ${gridRows * tile} px`;
                      })()
                    : `${exportResolution} px long edge`}
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.4rem', marginTop: '0.4rem' }}>
                <button
                  type="button"
                  className={`engine-pill-btn ${exportResolution === 4000 ? 'active' : ''}`}
                  onClick={() => setExportResolution(4000)}
                  style={{ padding: '0.55rem 0.2rem', flexDirection: 'column', gap: '0.2rem', textAlign: 'center' }}
                >
                  <span style={{ fontWeight: 700, fontSize: '0.74rem' }}>Standard Web</span>
                  <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>4000 px long edge</span>
                </button>
                <button
                  type="button"
                  className={`engine-pill-btn ${exportResolution === 8000 ? 'active' : ''}`}
                  onClick={() => setExportResolution(8000)}
                  style={{ padding: '0.55rem 0.2rem', flexDirection: 'column', gap: '0.2rem', textAlign: 'center' }}
                >
                  <span style={{ fontWeight: 700, fontSize: '0.74rem' }}>High-Res Print</span>
                  <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>8000 px long edge</span>
                </button>
                <button
                  type="button"
                  className={`engine-pill-btn ${exportResolution === 12000 ? 'active' : ''}`}
                  onClick={() => setExportResolution(12000)}
                  style={{ padding: '0.55rem 0.2rem', flexDirection: 'column', gap: '0.2rem', textAlign: 'center' }}
                >
                  <span style={{ fontWeight: 700, fontSize: '0.74rem' }}>Ultra Gigapixel</span>
                  <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>12000 px long edge</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {selectedMode === 'ascii' && (
          <div className="inspector-card">
            <div className="inspector-card-header">
              <span className="inspector-card-title">Sobel ASCII Parameters</span>
            </div>

            <div className="param-row">
              <div className="param-label-bar"><span>Color Theme</span></div>
              <div className="tag-chip-group">
                {[
                  { id: 'cyan', label: 'Cyber Cyan' },
                  { id: 'green', label: 'Phosphor Green' },
                  { id: 'amber', label: 'Amber CRT' },
                  { id: 'mono', label: 'Monochrome' },
                  { id: 'truecolor', label: 'True Color (Source Photo)' },
                ].map((t) => (
                  <button
                    key={t.id}
                    className={`tag-toggle-btn ${asciiTheme === t.id ? 'active' : ''}`}
                    onClick={() => setAsciiTheme(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="param-row">
              <div className="param-label-bar"><span>Character Glyph Density</span></div>
              <div className="tag-chip-group">
                {[
                  { id: 'extended', label: '70-Glyph Edge Ramp' },
                  { id: 'standard', label: '10-Glyph Contrast' },
                  { id: 'blocks', label: 'Unicode Blocks (░▒▓█)' },
                ].map((r) => (
                  <button
                    key={r.id}
                    className={`tag-toggle-btn ${asciiRamp === r.id ? 'active' : ''}`}
                    onClick={() => setAsciiRamp(r.id)}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="param-row">
              <div className="param-label-bar">
                <span>Grid Columns</span>
                <span className="param-val-bubble">{gridDensity} cols (Aspect Normalized)</span>
              </div>
              <input
                type="range"
                className="custom-range"
                min="40"
                max="140"
                step="5"
                value={gridDensity}
                onChange={(e) => setGridDensity(Number(e.target.value))}
              />
            </div>
          </div>
        )}

        {selectedMode === 'kuwahara' && (
          <div className="inspector-card">
            <div className="inspector-card-header">
              <span className="inspector-card-title">Kuwahara Oil Paint</span>
            </div>

            <div className="param-row">
              <div className="param-label-bar">
                <span>Quadrant Radius</span>
                <span className="param-val-bubble">{kuwaharaRadius} px</span>
              </div>
              <input
                type="range"
                className="custom-range"
                min="2"
                max="8"
                value={kuwaharaRadius}
                onChange={(e) => setKuwaharaRadius(Number(e.target.value))}
              />
            </div>
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '0.4rem', lineHeight: 1.4 }}>
              Each pixel takes the average colour of its calmest neighbouring quadrant: larger radii give broader brush patches.
            </div>
          </div>
        )}

        {selectedMode === 'flowfield' && (
          <div className="inspector-card">
            <div className="inspector-card-header">
              <span className="inspector-card-title">Vector Flow Field</span>
            </div>

            <div className="param-row">
              <div className="param-label-bar">
                <span>Stroke Length</span>
                <span className="param-val-bubble">{stepLength} steps</span>
              </div>
              <input
                type="range"
                className="custom-range"
                min="2"
                max="60"
                value={stepLength}
                onChange={(e) => setStepLength(Number(e.target.value))}
              />
            </div>

            <div className="param-row">
              <div className="param-label-bar">
                <span>Particles</span>
                <span className="param-val-bubble">{numParticles.toLocaleString()} strokes</span>
              </div>
              <input
                type="range"
                className="custom-range"
                min="500"
                max="30000"
                step="500"
                value={numParticles}
                onChange={(e) => setNumParticles(Number(e.target.value))}
              />
            </div>

            <div className="param-row">
              <div className="param-label-bar">
                <span>Stroke Width</span>
                <span className="param-val-bubble">{strokeWidth} px</span>
              </div>
              <input
                type="range"
                className="custom-range"
                min="1"
                max="8"
                value={strokeWidth}
                onChange={(e) => setStrokeWidth(Number(e.target.value))}
              />
            </div>
          </div>
        )}

        {/* Dynamic Alpha-Tinting Blend */}
        <div className="inspector-card">
          <div className="inspector-card-header">
            <span className="inspector-card-title">Dynamic Alpha Tinting</span>
            <span className="param-val-bubble">{alphaTint}%</span>
          </div>
          <div className="param-row">
            <input
              type="range"
              className="custom-range"
              min="0"
              max="50"
              value={alphaTint}
              onChange={(e) => setAlphaTint(Number(e.target.value))}
            />
            <div style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', marginTop: '0.35rem', fontFamily: 'var(--font-mono)' }}>
              Blend formula: (1 - &alpha;) &bull; Mosaic + &alpha; &bull; Target
            </div>
            {jobCompleted && (
              <div style={{ fontSize: '0.7rem', color: 'var(--accent-green)', marginTop: '0.3rem' }}>
                Adjust live on the finished artboard; Export uses this tint.
              </div>
            )}
          </div>
        </div>

        {/* Dispatch Action Button */}
        <Notice notice={jobNotice} onDismiss={() => setJobNotice(null)} />
        <button
          id="btnDispatchJob"
          className="btn-synthesize-cta"
          onClick={handleSubmitJob}
          disabled={!targetFile || isProcessing || (selectedMode === 'photomosaic' && !isPoolConfigValid)}
        >
          {isProcessing ? 'Distributed Compute in Progress...' : 'Synthesize Master Artboard'}
        </button>

        {jobCompleted && jobId && (
          <div className="inspector-card" style={{ padding: '0.85rem' }}>
            <div className="inspector-card-header" style={{ marginBottom: '0.5rem' }}>
              <span className="inspector-card-title">Export Full Resolution</span>
              {lastJobDims && <span className="param-val-bubble">{lastJobDims.width} &times; {lastJobDims.height} px</span>}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem', marginBottom: '0.6rem' }}>
              {[
                { id: 'png', label: 'PNG', sub: 'lossless, larger' },
                { id: 'jpg', label: 'JPEG', sub: 'high quality, ~5× smaller' },
              ].map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className={`engine-pill-btn ${exportFormat === f.id ? 'active' : ''}`}
                  style={{ padding: '0.45rem 0.2rem', flexDirection: 'column', gap: '0.1rem' }}
                  onClick={() => setExportFormat(f.id)}
                  disabled={exportState?.busy}
                >
                  <span style={{ fontWeight: 700, fontSize: '0.74rem' }}>{f.label}</span>
                  <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>{f.sub}</span>
                </button>
              ))}
            </div>
            <button
              className="btn-action-primary btn-cyan-ghost"
              style={{ width: '100%', padding: '0.7rem' }}
              onClick={exportArtboard}
              disabled={exportState?.busy}
            >
              <IconDownload size={16} />
              <span>{exportState?.busy ? 'Preparing export…' : `Download ${exportFormat.toUpperCase()}`}</span>
            </button>
            {exportState?.busy && exportState.pct != null && (
              <div style={{ height: 5, background: 'rgba(255,255,255,0.08)', borderRadius: 999, overflow: 'hidden', marginTop: '0.5rem' }}>
                <div style={{ width: `${exportState.pct}%`, height: '100%', background: 'var(--accent-cyan)', transition: 'width 0.2s ease' }} />
              </div>
            )}
            {exportState?.text && (
              <div
                role={exportState.error ? 'alert' : 'status'}
                style={{
                  fontSize: '0.7rem',
                  marginTop: '0.5rem',
                  lineHeight: 1.4,
                  color: exportState.error ? 'var(--accent-pink)' : exportState.done ? 'var(--accent-green)' : 'var(--text-secondary)'
                }}
              >
                {exportState.text}
              </div>
            )}
            <a
              href={exportUrl(exportFormat, false)}
              target="_blank"
              rel="noopener noreferrer"
              style={{ display: 'block', fontSize: '0.7rem', color: 'var(--accent-cyan)', marginTop: '0.45rem' }}
            >
              View full resolution in a new tab ↗
            </a>
          </div>
        )}
      </div>

      {/* RIGHT VIEWPORT WORKSPACE */}
      <div className="canvas-viewport-area">
        {/* Workspace Floating Header HUD */}
        <div className="viewport-header-hud">
          <div className="hud-group">
            <button className="hud-btn" onClick={() => setZoomLevel((z) => Math.min(3.5, z + 0.2))}>+</button>
            <button className="hud-btn" onClick={() => setZoomLevel((z) => Math.max(0.3, z - 0.2))}>&minus;</button>
            <button className="hud-btn" onClick={resetViewport}>1:1 Reset</button>
            <span className="hud-val-pill">{Math.round(zoomLevel * 100)}%</span>
            {inspectable && (
              <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                Click a tile to inspect{zoomLevel > DETAIL_ZOOM ? ' · high-res tiles on' : ''}
              </span>
            )}
          </div>

          <div className="hud-group">
            {finalMosaicUrl && (
              <span style={{ fontSize: '0.75rem', color: 'var(--accent-green)', fontFamily: 'var(--font-mono)', display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                <IconCheck size={14} /> Final Artboard Stitched
              </span>
            )}
            {jobCompleted && jobId && (
              <button className="hud-btn" onClick={exportArtboard} disabled={exportState?.busy}>
                <IconDownload size={14} />
                <span>{exportState?.busy ? 'Exporting…' : 'Export High-Res'}</span>
              </button>
            )}
          </div>
        </div>

        {/* Interactive Pan/Zoom Canvas Stage */}
        <div
          ref={stageRef}
          className={`canvas-interactive-stage ${isPanning ? 'is-panning' : ''}`}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onWheel={handleWheel}
        >
          {/* Requirement 4: STREAMLINED MINIMALIST PROGRESS PILL DOCKED AT TOP CENTER */}
          {isProcessing && (
            <div className="floating-progress-pill" style={{
              position: 'absolute',
              top: '1.25rem',
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 30,
              background: 'rgba(10, 14, 24, 0.85)',
              backdropFilter: 'blur(8px)',
              WebkitBackdropFilter: 'blur(8px)',
              border: '1px solid rgba(0, 229, 255, 0.38)',
              borderRadius: '30px',
              padding: '0.45rem 1.25rem',
              display: 'flex',
              alignItems: 'center',
              gap: '0.9rem',
              boxShadow: '0 8px 32px rgba(0, 0, 0, 0.65), 0 0 16px rgba(0, 229, 255, 0.2)',
              pointerEvents: 'none'
            }}>
              <div style={{
                width: '160px',
                height: '7px',
                background: 'rgba(255, 255, 255, 0.12)',
                borderRadius: '4px',
                overflow: 'hidden'
              }}>
                <div style={{
                  width: `${jobProgress}%`,
                  height: '100%',
                  background: 'linear-gradient(90deg, #00e5ff 0%, #00ffa3 100%)',
                  borderRadius: '4px',
                  transition: 'width 0.3s ease',
                  boxShadow: '0 0 10px rgba(0, 255, 163, 0.8)'
                }} />
              </div>
              <span style={{
                fontFamily: 'var(--font-mono, monospace)',
                fontWeight: 800,
                fontSize: '0.82rem',
                color: '#00ffa3',
                minWidth: '38px'
              }}>
                {jobProgress}%
              </span>
              <span style={{
                fontFamily: 'var(--font-mono, monospace)',
                fontSize: '0.78rem',
                color: 'rgba(255, 255, 255, 0.85)',
                borderLeft: '1px solid rgba(255, 255, 255, 0.15)',
                paddingLeft: '0.75rem',
                whiteSpace: 'nowrap'
              }}>
                {assemblyPhase === 'assembling' ? 'Assembling tiles…' : `${completedChunks} / ${totalChunks} Chunks Processed`}
              </span>
            </div>
          )}

          {/* Canvas Mount - Always visible and active from 0% */}
          <div
            ref={mountRef}
            className="canvas-shadow-mount"
            style={{
              transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${zoomLevel})`,
              transformOrigin: 'center center',
              position: 'relative',
              display: (targetPreviewUrl || finalMosaicUrl) ? 'inline-block' : 'none',
              lineHeight: 0
            }}
          >
            <canvas
              ref={canvasRef}
              style={{
                display: 'block',
                maxWidth: '85vw',
                maxHeight: '75vh',
                width: 'auto',
                height: 'auto',
                imageRendering: 'auto',
                borderRadius: '4px'
              }}
            />

            {detailTiles.length > 0 && jobDimsRef.current && (
              <div className="tile-detail-layer" aria-hidden="true">
                {detailTiles.map((c) => {
                  const W = jobDimsRef.current.width;
                  const H = jobDimsRef.current.height;
                  return (
                    <div
                      key={c.cell_index ?? `${c.col},${c.row}`}
                      style={{
                        left: `${(c.px / W) * 100}%`,
                        top: `${(c.py / H) * 100}%`,
                        width: `${(c.w / W) * 100}%`,
                        height: `${(c.h / H) * 100}%`,
                      }}
                    >
                      <img
                        src={`${GATEWAY_URL}${tileUrlOf(c.thumb)}`}
                        alt=""
                        decoding="async"
                        onError={(ev) => {
                          if (!ev.currentTarget.dataset.fallback) {
                            ev.currentTarget.dataset.fallback = '1';
                            ev.currentTarget.src = `${GATEWAY_URL}${c.thumb}`;
                          }
                        }}
                      />
                      {alphaTint > 0 && targetPreviewUrl && (
                        <div
                          className="tile-detail-tint"
                          style={{
                            opacity: Math.min(0.5, alphaTint / 100),
                            backgroundImage: `url(${targetPreviewUrl})`,
                            backgroundSize: `${(W / c.w) * 100}% ${(H / c.h) * 100}%`,
                            backgroundPosition: `${W > c.w ? (c.px / (W - c.w)) * 100 : 0}% ${H > c.h ? (c.py / (H - c.h)) * 100 : 0}%`,
                          }}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {!targetPreviewUrl && !finalMosaicUrl && !isProcessing && (
            <div style={{ position: 'absolute', textAlign: 'center', color: 'var(--text-tertiary)' }}>
              <IconGrid size={36} style={{ margin: '0 auto 0.6rem', opacity: 0.3 }} />
              <div style={{ fontSize: '0.92rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                Raster Canvas Idle
              </div>
              <div style={{ fontSize: '0.78rem' }}>Upload a master image in the inspector panel to begin</div>
            </div>
          )}
        </div>
      </div>

      {inspect && (
        <div className="tile-inspect-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setInspect(null); }}>
          <div className="tile-inspect-modal" role="dialog" aria-modal="true" aria-labelledby="tile-inspect-title">
            <button type="button" className="tile-inspect-close" onClick={() => setInspect(null)} aria-label="Close">&times;</button>
            <div className="tile-inspect-photo">
              {inspect.data ? (
                <img src={`${GATEWAY_URL}${inspect.data.original_url}`} alt={`Original photo for tile ${inspect.cell.col}, ${inspect.cell.row}`} />
              ) : (
                <span style={{ color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>
                  {inspect.loading ? 'Loading original…' : inspect.error}
                </span>
              )}
            </div>
            <div className="tile-inspect-details">
              <h3 id="tile-inspect-title">Inspect Tile</h3>
              {inspect.data && (
                <dl>
                  <dt>ΔE₀₀</dt>
                  <dd>
                    <div className="tile-inspect-score">{inspect.data.delta_e != null ? inspect.data.delta_e.toFixed(2) : '—'}</div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>{matchLabel(inspect.data.delta_e)}</div>
                  </dd>
                  <dt>Contributor</dt>
                  <dd>{inspect.data.contributor || (inspect.data.source === 'personal' ? 'Your vault' : 'Anonymous donor')}</dd>
                  <dt>Source</dt>
                  <dd>{inspect.data.source === 'personal' ? 'Personal vault' : 'Community library'}</dd>
                  <dt>Colour</dt>
                  <dd className="tile-inspect-swatches">
                    {inspect.data.target_rgb && <span title="Target colour" style={{ background: `rgb(${inspect.data.target_rgb.join(',')})` }} />}
                    <span aria-hidden="true">→</span>
                    {inspect.data.matched_rgb && <span title="Photo colour" style={{ background: `rgb(${inspect.data.matched_rgb.map(Math.round).join(',')})` }} />}
                  </dd>
                  <dt>Tile</dt>
                  <dd>column {inspect.data.col + 1}, row {inspect.data.row + 1}</dd>
                  {inspect.data.tags?.length > 0 && (<><dt>Tags</dt><dd>{inspect.data.tags.join(', ')}</dd></>)}
                  <dt>Photo ID</dt>
                  <dd style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem' }}>{inspect.data.photo_id}</dd>
                </dl>
              )}
              {inspect.data && (
                <a href={`${GATEWAY_URL}${inspect.data.original_url}`} target="_blank" rel="noopener noreferrer"
                  style={{ display: 'inline-block', marginTop: '1rem', fontSize: '0.78rem', color: 'var(--accent-cyan)' }}>
                  Open full resolution ↗
                </a>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
