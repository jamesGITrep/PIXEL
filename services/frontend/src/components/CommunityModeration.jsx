import React, { useCallback, useEffect, useState } from 'react';
import { IconTrash } from './Icons';

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';
const PAGE_SIZE = 60;

async function readError(res) {
  try { return (await res.json()).detail || `HTTP ${res.status}`; } catch { return `HTTP ${res.status}`; }
}

/**
 * Community Pool Moderation: grid of every public tile with a two-step delete.
 * Deleting needs a signed-in account listed in the gateway's ADMIN_USERNAMES.
 */
export default function CommunityModeration({ user, onRequestSignIn }) {
  const [photos, setPhotos] = useState([]);
  const [total, setTotal] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmId, setConfirmId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const load = useCallback(async (offset = 0) => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/admin/community/photos?offset=${offset}&limit=${PAGE_SIZE}`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setTotal(data.total);
      setPhotos((prev) => (offset === 0 ? data.photos : [...prev, ...data.photos]));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user?.is_admin) load(0);
  }, [user, load]);

  const remove = async (photo) => {
    setDeletingId(photo.id);
    setNotice('');
    try {
      const res = await fetch(`${GATEWAY_URL}/api/v1/admin/community/photos/${encodeURIComponent(photo.id)}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      setPhotos((prev) => prev.filter((p) => p.id !== photo.id));
      setTotal(data.total_photos);
      setNotice(`Deleted photo ${photo.id} (${data.deleted_objects} stored files removed).`);
    } catch (e) {
      setError(`Delete failed: ${e.message}`);
    } finally {
      setDeletingId(null);
      setConfirmId(null);
    }
  };

  return (
    <section className="control-card moderation-card" aria-labelledby="moderation-title">
      <div className="moderation-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <h2 id="moderation-title" className="card-title" style={{ margin: 0 }}>Community Pool Moderation</h2>
          {user?.is_admin && <span className="moderation-count">{total === null ? '…' : `${total.toLocaleString()} photos`}</span>}
        </div>
        {user?.is_admin && (
          <button className="hud-btn" onClick={() => load(0)} disabled={loading}>
            {loading ? 'Refreshing…' : '↻ Refresh'}
          </button>
        )}
      </div>

      {!user && (
        <p className="moderation-gate">
          Sign in with an administrator account to review and remove community photos.{' '}
          <button className="moderation-link" onClick={() => onRequestSignIn?.('login')}>Sign in</button>
        </p>
      )}
      {user && !user.is_admin && (
        <p className="moderation-gate">
          Signed in as <strong>{user.username}</strong>, which is not an administrator account.
        </p>
      )}

      {user?.is_admin && (
        <>
          {error && <div role="alert" className="moderation-error">{error}</div>}
          {notice && <div role="status" className="moderation-notice">{notice}</div>}
          {photos.length === 0 && !loading && !error && (
            <p className="moderation-gate">The community pool is empty.</p>
          )}
          <ul className="moderation-grid">
            {photos.map((p) => (
              <li key={p.id} className={`moderation-tile ${confirmId === p.id ? 'is-confirming' : ''}`}>
                <img src={`${GATEWAY_URL}${p.tile_url}`} alt={`Community photo ${p.id}`} loading="lazy"
                  onError={(e) => { if (!e.currentTarget.dataset.fallback) { e.currentTarget.dataset.fallback = '1'; e.currentTarget.src = `${GATEWAY_URL}${p.thumb_url}`; } }} />
                <div className="moderation-meta">
                  <span title={p.tags.join(', ')}>{p.tags.length ? p.tags.join(', ') : 'Untagged'}</span>
                  <span>{p.contributor ? `by ${p.contributor}` : 'Anonymous donor'}</span>
                </div>
                {confirmId === p.id ? (
                  <div className="moderation-confirm" role="alertdialog" aria-label={`Delete photo ${p.id}?`}>
                    <span>Delete permanently?</span>
                    <div>
                      <button onClick={() => setConfirmId(null)} disabled={deletingId === p.id}>Cancel</button>
                      <button className="danger" onClick={() => remove(p)} disabled={deletingId === p.id}>
                        {deletingId === p.id ? 'Deleting…' : 'Delete'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <button className="moderation-delete" onClick={() => { setConfirmId(p.id); setNotice(''); }}
                    aria-label={`Delete photo ${p.id}`} title="Delete Photo">
                    <IconTrash size={14} />
                    <span>Delete Photo</span>
                  </button>
                )}
              </li>
            ))}
          </ul>
          {total !== null && photos.length < total && (
            <button className="hud-btn" style={{ marginTop: '1rem' }} onClick={() => load(photos.length)} disabled={loading}>
              {loading ? 'Loading…' : `Load more (${(total - photos.length).toLocaleString()} remaining)`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
