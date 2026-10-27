import React, { useCallback, useEffect, useState } from 'react';
import { IconTrash } from './Icons';

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';
const MB = 1024 * 1024;

async function readError(res) {
  try { return (await res.json()).detail || `HTTP ${res.status}`; } catch { return `HTTP ${res.status}`; }
}

const formatDate = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
};

/**
 * Self-service account controls: summary, vault management and the danger zone.
 * Rendered only for signed-in users (App redirects guests to the home page + sign-in dialog).
 */
export default function AccountPage({ user, onAccountDeleted, onNavigateToStudio }) {
  const [summary, setSummary] = useState(null);
  const [photos, setPhotos] = useState([]);
  const [selected, setSelected] = useState(() => new Set());
  const [loadError, setLoadError] = useState('');
  const [vaultNotice, setVaultNotice] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [purging, setPurging] = useState(false);
  const [purgeError, setPurgeError] = useState('');

  const load = useCallback(async () => {
    setLoadError('');
    try {
      const [sRes, pRes] = await Promise.all([
        fetch(`${GATEWAY_URL}/api/v1/users/me`, { credentials: 'same-origin' }),
        fetch(`${GATEWAY_URL}/api/v1/library/personal`, { credentials: 'same-origin' }),
      ]);
      if (!sRes.ok) throw new Error(await readError(sRes));
      if (!pRes.ok) throw new Error(await readError(pRes));
      setSummary(await sRes.json());
      const vault = await pRes.json();
      setPhotos(vault.photos || []);
      setSelected((prev) => new Set([...prev].filter((id) => (vault.photos || []).some((p) => p.id === id))));
    } catch (e) {
      setLoadError(`Couldn't load your account: ${e.message}`);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = (id) => {
    setConfirmingDelete(false);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const allSelected = photos.length > 0 && selected.size === photos.length;
  const toggleAll = () => {
    setConfirmingDelete(false);
    setSelected(allSelected ? new Set() : new Set(photos.map((p) => p.id)));
  };

  const deleteSelected = async () => {
    setDeleting(true);
    setVaultNotice(null);
    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/library/personal/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ photo_ids: [...selected] }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setSelected(new Set());
      await load();
      setVaultNotice({ type: 'success', text: `${data.deleted.length} photo${data.deleted.length === 1 ? '' : 's'} permanently deleted.` });
    } catch (e) {
      setVaultNotice({ type: 'error', text: `Delete failed: ${e.message}` });
    } finally {
      setDeleting(false);
      setConfirmingDelete(false);
    }
  };

  const purgeAccount = async (e) => {
    e.preventDefault();
    setPurging(true);
    setPurgeError('');
    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/users/me/purge`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ confirm: confirmText }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      onAccountDeleted(data.deleted_photos);
    } catch (err) {
      setPurgeError(err.message);
      setPurging(false);
    }
  };

  const usedMb = summary ? summary.bytes_used / MB : 0;
  const quotaMb = summary ? summary.quota_bytes / MB : 0;
  const usedPct = summary && quotaMb ? Math.min(100, (usedMb / quotaMb) * 100) : 0;

  return (
    <div className="account-page">
      <header className="account-header">
        <span className="hero-badge">Account Settings</span>
        <h1>Your account</h1>
        <p>Everything PixelMosaic stores for you, and the controls to remove it.</p>
      </header>

      {loadError && <div role="alert" className="account-alert error">{loadError}</div>}

      {/* Account summary */}
      <section className="account-card" aria-labelledby="acct-summary">
        <h2 id="acct-summary">Account summary</h2>
        <dl className="account-summary">
          <div><dt>Username</dt><dd>{user.username}</dd></div>
          <div><dt>Email</dt><dd>{user.email}</dd></div>
          <div><dt>Account created</dt><dd>{formatDate(user.created_at)}</dd></div>
          <div><dt>Total stored photos</dt><dd>{summary ? summary.photo_count.toLocaleString() : '…'}</dd></div>
          <div className="account-quota">
            <dt>Storage quota used</dt>
            <dd>
              {summary ? `${usedMb.toFixed(usedMb < 10 ? 2 : 1)} MB of ${quotaMb.toFixed(0)} MB` : '…'}
              <div className="account-quota-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100}
                aria-valuenow={Math.round(usedPct)} aria-label="Storage quota used">
                <span style={{ width: `${usedPct}%` }} className={usedPct > 90 ? 'is-full' : ''} />
              </div>
            </dd>
          </div>
        </dl>
      </section>

      {/* Vault management */}
      <section className="account-card" aria-labelledby="acct-vault">
        <div className="account-card-head">
          <h2 id="acct-vault">Vault management</h2>
          {photos.length > 0 && (
            <div className="account-vault-actions">
              <label className="account-select-all">
                <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                Select all
              </label>
              {confirmingDelete ? (
                <>
                  <span className="account-confirm-text">Delete {selected.size} permanently?</span>
                  <button className="account-btn" onClick={() => setConfirmingDelete(false)} disabled={deleting}>Cancel</button>
                  <button className="account-btn danger" onClick={deleteSelected} disabled={deleting}>
                    {deleting ? 'Deleting…' : 'Delete'}
                  </button>
                </>
              ) : (
                <button className="account-btn danger" onClick={() => setConfirmingDelete(true)} disabled={selected.size === 0}>
                  <IconTrash size={13} /> Delete selected ({selected.size})
                </button>
              )}
            </div>
          )}
        </div>
        {vaultNotice && <div role={vaultNotice.type === 'error' ? 'alert' : 'status'} className={`account-alert ${vaultNotice.type}`}>{vaultNotice.text}</div>}
        {summary?.pending_count > 0 && (
          <p className="account-muted">{summary.pending_count} uploaded photo{summary.pending_count === 1 ? ' is' : 's are'} still being analysed.</p>
        )}
        {photos.length === 0 ? (
          <p className="account-muted">
            Your vault is empty.{' '}
            <button className="account-link" onClick={onNavigateToStudio}>Add photos in the Studio</button>
          </p>
        ) : (
          <ul className="account-grid">
            {photos.map((p) => (
              <li key={p.id} className={selected.has(p.id) ? 'is-selected' : ''}>
                <label>
                  <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggle(p.id)}
                    aria-label={`Select photo ${p.filename || p.id}`} />
                  <img src={`${GATEWAY_URL}${p.thumb_url.replace('thumbs-50x50/', 'tiles-256/')}`} alt="" loading="lazy"
                    onError={(ev) => { if (!ev.currentTarget.dataset.fallback) { ev.currentTarget.dataset.fallback = '1'; ev.currentTarget.src = `${GATEWAY_URL}${p.thumb_url}`; } }} />
                  {p.tags?.length > 0 && <span className="account-tile-tags">{p.tags.join(', ')}</span>}
                </label>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Danger zone */}
      <section className="account-danger" aria-labelledby="acct-danger">
        <h2 id="acct-danger">Danger zone</h2>
        <h3>Permanently Delete Account &amp; Personal Photos</h3>
        <p>
          Deletes your account and every photo in your vault ({summary ? summary.photo_count.toLocaleString() : '…'} photos).
          You will be signed out on every device. This cannot be undone. Mosaics you already exported are not affected.
        </p>
        <form onSubmit={purgeAccount}>
          <label htmlFor="purge-confirm">
            Type your username <strong>{user.username}</strong> or your password to confirm
          </label>
          <div className="account-danger-row">
            <input id="purge-confirm" type="password" value={confirmText} autoComplete="off"
              onChange={(e) => { setConfirmText(e.target.value); setPurgeError(''); }} />
            <button type="submit" className="account-btn danger solid" disabled={!confirmText.trim() || purging}>
              {purging ? 'Deleting everything…' : 'Delete All My Data'}
            </button>
          </div>
          {purgeError && <div role="alert" className="account-alert error">{purgeError}</div>}
        </form>
      </section>
    </div>
  );
}
