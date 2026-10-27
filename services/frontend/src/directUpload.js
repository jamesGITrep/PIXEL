// BRD 4.1 drag-and-drop ingestion through the single API Gateway.
// The gateway issues one single-use upload URL per file; the browser PUTs each file to it and the
// gateway streams it into the private bucket. Workers then analyse each upload and add it to the
// photo index. Storage is never reachable from the browser.

// Same-origin: the frontend's edge proxy forwards /api/* to the API Gateway. Browsers never
// address the gateway, storage or any other service directly.
const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';
const URL_BATCH = 100;
const PUT_CONCURRENCY = 6;

export const isImageFile = (f) => f.type.startsWith('image/') || /\.(jpe?g|png|webp)$/i.test(f.name);

/**
 * @param {File[]} files
 * @param {{pool: 'personal'|'community', tags?: string[]}} target  (personal = the signed-in user's vault)
 * @param {(done: number, total: number) => void} onProgress
 * @returns {Promise<{uploaded: number, failed: string[]}>}
 */
export async function directUpload(files, { pool, tags = [] }, onProgress) {
  let done = 0;
  let uploaded = 0;
  const failed = [];

  for (let i = 0; i < files.length; i += URL_BATCH) {
    const batch = files.slice(i, i + URL_BATCH);
    const res = await fetch(`${GATEWAY_URL}/api/v1/library/upload-urls`, {
      method: 'POST',
      credentials: GATEWAY_URL ? 'include' : 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pool,
        tags,
        files: batch.map((f) => ({ filename: f.name, content_type: f.type || 'application/octet-stream' })),
      }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json()).detail || detail; } catch { /* keep status */ }
      throw new Error(detail);
    }
    const { uploads } = await res.json();

    // PUT with bounded concurrency
    let next = 0;
    const worker = async () => {
      while (next < uploads.length) {
        const idx = next++;
        const up = uploads[idx];
        const file = batch[idx];
        try {
          if (up.error) throw new Error(up.error);
          const put = await fetch(`${GATEWAY_URL}${up.upload_url}`, {
            method: 'PUT',
            headers: { 'Content-Type': up.content_type },
            body: file,
          });
          if (!put.ok) {
            let detail = `HTTP ${put.status}`;
            try { detail = (await put.json()).detail || detail; } catch { /* keep status */ }
            throw new Error(detail);
          }
          uploaded += 1;
        } catch (err) {
          failed.push(`${file.name} (${err.message})`);
        } finally {
          done += 1;
          onProgress?.(done, files.length);
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(PUT_CONCURRENCY, uploads.length) }, worker));
  }
  return { uploaded, failed };
}
