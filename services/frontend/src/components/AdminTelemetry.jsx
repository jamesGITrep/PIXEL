import React, { useEffect, useMemo, useState } from 'react';
import CommunityModeration from './CommunityModeration';

// Same-origin: the frontend's edge proxy forwards /api/* to the API Gateway. Browsers never
// address the gateway, storage or any other service directly.
const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';
const TELEMETRY_POLL_INTERVAL = 5000; // 5 seconds

const fmt = (n, digits = 0) =>
  n === null || n === undefined || Number.isNaN(n) ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });

const ago = (seconds) => {
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
};

const uptime = (s) => (s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`);

/** Single-series time chart (one measure per chart: no dual axes). Hover shows a crosshair + value. */
function TimeChart({ title, points, unit = '', yMax, step = false, digits = 0, now }) {
  const [hover, setHover] = useState(null);
  const W = 600;
  const H = 150;
  const pad = { l: 36, r: 10, t: 10, b: 22 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  if (!points.length) {
    return (
      <div className="control-card" style={{ padding: '1rem 1.25rem' }}>
        <div className="card-title" style={{ marginBottom: '0.5rem' }}>{title}</div>
        <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem', padding: '2rem 0', textAlign: 'center' }}>Collecting samples…</div>
      </div>
    );
  }

  const t0 = points[0].t;
  const t1 = Math.max(now, points[points.length - 1].t);
  const span = Math.max(1, t1 - t0);
  const maxV = Math.max(yMax ?? 0, ...points.map((p) => p.v), 1);
  const x = (t) => pad.l + ((t - t0) / span) * iw;
  const y = (v) => pad.t + ih - (v / maxV) * ih;

  let d = '';
  points.forEach((p, i) => {
    if (i === 0) d += `M${x(p.t)},${y(p.v)}`;
    else if (step) d += `H${x(p.t)}V${y(p.v)}`;
    else d += `L${x(p.t)},${y(p.v)}`;
  });
  if (step) d += `H${x(t1)}`;

  const ticks = [0, 0.5, 1].map((f) => f * maxV);
  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const t = t0 + ((px - pad.l) / iw) * span;
    let best = points[0];
    for (const p of points) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    setHover(best);
  };
  const last = points[points.length - 1];

  return (
    <div className="control-card" style={{ padding: '1rem 1.25rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.4rem' }}>
        <div className="card-title">{title}</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: 'var(--text-primary)' }}>
          {hover ? `${fmt(hover.v, digits)}${unit} · ${ago(now - hover.t)}` : `now ${fmt(last.v, digits)}${unit}`}
        </div>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto', display: 'block', cursor: 'crosshair' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={`${title}: currently ${fmt(last.v, digits)}${unit}`}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="rgba(255,255,255,0.07)" strokeWidth="1" />
            <text x={pad.l - 6} y={y(v) + 3} textAnchor="end" fontSize="10" fill="var(--text-muted)">{fmt(v, v < 10 ? 1 : 0)}</text>
          </g>
        ))}
        <text x={pad.l} y={H - 6} fontSize="10" fill="var(--text-muted)">{ago(now - t0)}</text>
        <text x={W - pad.r} y={H - 6} fontSize="10" fill="var(--text-muted)" textAnchor="end">now</text>
        <path d={d} fill="none" stroke="var(--accent-cyan)" strokeWidth="2" strokeLinejoin="round" />
        {hover && (
          <g pointerEvents="none">
            <line x1={x(hover.t)} x2={x(hover.t)} y1={pad.t} y2={pad.t + ih} stroke="rgba(255,255,255,0.35)" strokeWidth="1" />
            <circle cx={x(hover.t)} cy={y(hover.v)} r="4.5" fill="var(--accent-cyan)" stroke="#0a0c12" strokeWidth="2" />
          </g>
        )}
      </svg>
    </div>
  );
}

function Kpi({ label, value, sub }) {
  return (
    <div className="control-card" style={{ padding: '1rem 1.25rem' }}>
      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
      <div style={{ fontSize: '1.7rem', fontWeight: 800, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', marginTop: '0.2rem' }}>{value}</div>
      {sub && <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>{sub}</div>}
    </div>
  );
}

export default function AdminTelemetry({ user, onRequestSignIn }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now() / 1000);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${GATEWAY_URL}/api/v1/admin/telemetry`, {
          credentials: GATEWAY_URL ? 'include' : 'same-origin',
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (!cancelled) {
          setData(json);
          setNow(json.generated_at || Date.now() / 1000);
          setError('');
        }
      } catch (e) {
        if (!cancelled) setError(`Telemetry unavailable: ${e.message}`);
      }
    };
    load();
    const id = setInterval(load, TELEMETRY_POLL_INTERVAL);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const history = data?.history || [];
  const series = useMemo(() => ({
    nodes: history.map((h) => ({ t: h.t, v: h.nodes })),
    cpu: history.filter((h) => h.cpu !== null).map((h) => ({ t: h.t, v: h.cpu })),
    tiles: history.map((h) => ({ t: h.t, v: h.tiles_per_sec })),
  }), [history]);

  const s = data?.summary;
  // "How fast nodes scale up": spread of the most recent burst of joins (joins within 2 min of each other)
  const scaleUp = useMemo(() => {
    const joins = (data?.scale_events || []).filter((e) => e.type === 'join').map((e) => e.at).sort((a, b) => b - a);
    if (!joins.length) return null;
    let i = 0;
    while (i + 1 < joins.length && joins[i] - joins[i + 1] <= 120) i += 1;
    return { count: i + 1, seconds: joins[0] - joins[i], at: joins[0] };
  }, [data]);

  return (
    <div style={{ maxWidth: '1240px', margin: '2rem auto', padding: '0 1.5rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: '1rem', marginBottom: '1.5rem' }}>
        <div>
          <span className="hero-badge">Admin · System Observability</span>
          <h1 style={{ fontSize: '2rem', fontWeight: 900, marginTop: '0.5rem' }}>Cluster Health</h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.88rem', marginTop: '0.25rem' }}>
            Live from worker heartbeats in the coordination bucket · refreshes every {TELEMETRY_POLL_INTERVAL / 1000}s
            <span className={`live-pulse ${error ? 'is-paused' : ''}`} role="status">
              <span className="live-pulse-dot" aria-hidden="true" />
              {error ? `Paused • Retrying every ${TELEMETRY_POLL_INTERVAL / 1000}s` : `Live • Updates every ${TELEMETRY_POLL_INTERVAL / 1000}s`}
            </span>
            {data?.plane && <> · plane: <code>{data.plane}</code></>}
          </p>
        </div>
      </div>

      {error && (
        <div role="alert" style={{ marginBottom: '1rem', background: 'rgba(255, 42, 133, 0.1)', border: '1px solid rgba(255, 42, 133, 0.35)', borderRadius: '8px', padding: '0.7rem 1rem', color: 'var(--accent-pink)', fontSize: '0.85rem' }}>
          {error}
        </div>
      )}

      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginBottom: '1rem' }}>
        <Kpi label="Active worker nodes" value={fmt(s?.active_nodes)} sub={s ? `${s.busy_nodes} busy · ${s.active_nodes - s.busy_nodes} idle` : ''} />
        <Kpi label="Tiles processed / sec" value={fmt(s?.tiles_per_sec, 1)} sub="60s rolling, all nodes" />
        <Kpi label="Node CPU" value={s ? `${fmt(s.node_cpu_percent, 1)}%` : '—'} sub={s ? `worker processes avg ${fmt(s.avg_process_cpu_percent, 1)}% of a core` : ''} />
        <Kpi label="Queue depth" value={fmt(s?.queue_depth)} sub="runnable chunk tasks" />
        <Kpi
          label="Last scale-up"
          value={scaleUp ? `+${scaleUp.count}` : '—'}
          sub={scaleUp ? `${scaleUp.count > 1 ? `in ${fmt(scaleUp.seconds, 1)}s · ` : ''}${ago(now - scaleUp.at)}` : 'no joins in 30 min'}
        />
      </div>

      {/* Time series: one measure per chart */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '1rem', marginBottom: '1rem' }}>
        <TimeChart title="Active nodes (scaling)" points={series.nodes} step now={now} />
        <TimeChart title="Node CPU utilisation" points={series.cpu} unit="%" yMax={100} digits={1} now={now} />
        <TimeChart title="Tiles processed per second" points={series.tiles} digits={1} now={now} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)', gap: '1rem' }}>
        {/* Nodes table */}
        <div className="control-card" style={{ padding: '1rem 1.25rem', overflowX: 'auto' }}>
          <div className="card-title" style={{ marginBottom: '0.75rem' }}>Worker nodes</div>
          {data?.nodes?.length ? (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ color: 'var(--text-muted)', textAlign: 'left' }}>
                  {['Node', 'Status', 'Worker CPU', 'Tiles/s', 'Cells done', 'Photos ingested', 'Uptime'].map((h) => (
                    <th key={h} style={{ padding: '0.4rem 0.5rem', fontWeight: 600, borderBottom: '1px solid var(--border-subtle)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.nodes.map((n) => (
                  <tr key={n.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <td style={{ padding: '0.45rem 0.5rem', fontFamily: 'var(--font-mono)' }} title={n.id}>{n.host}</td>
                    <td style={{ padding: '0.45rem 0.5rem' }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
                        <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: '50%', background: n.status === 'BUSY' ? 'var(--accent-green)' : 'var(--text-muted)' }} />
                        {n.status === 'BUSY' ? 'Busy' : 'Idle'}
                      </span>
                      {n.current_task && <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{n.current_task}</div>}
                    </td>
                    <td style={{ padding: '0.45rem 0.5rem', fontFamily: 'var(--font-mono)' }}>{fmt(n.process_cpu_percent, 1)}%</td>
                    <td style={{ padding: '0.45rem 0.5rem', fontFamily: 'var(--font-mono)' }}>{fmt(n.tiles_per_sec, 1)}</td>
                    <td style={{ padding: '0.45rem 0.5rem', fontFamily: 'var(--font-mono)' }}>{fmt(n.cells_total)}</td>
                    <td style={{ padding: '0.45rem 0.5rem', fontFamily: 'var(--font-mono)' }}>{fmt(n.photos_ingested_total)}</td>
                    <td style={{ padding: '0.45rem 0.5rem', fontFamily: 'var(--font-mono)' }}>{uptime(n.uptime_sec)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '1rem 0' }}>
              No worker heartbeats in the last 15 seconds. Start workers with <code>docker compose up -d --scale worker=3</code>.
            </div>
          )}
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.6rem' }}>
            Worker CPU = the worker process's CPU time as % of one core. Lifetime totals: {fmt(s?.cells_total)} cells,
            {' '}{fmt(s?.chunks_total)} chunks, {fmt(s?.photos_ingested_total)} photos ingested.
          </div>
        </div>

        <div style={{ display: 'grid', gap: '1rem', alignContent: 'start' }}>
          {/* Per-core CPU of the host machine */}
          <div className="control-card" style={{ padding: '1rem 1.25rem' }}>
            <div className="card-title" style={{ marginBottom: '0.6rem' }}>Per-core CPU (host)</div>
            {s?.per_core_cpu_percent?.length ? (
              <div style={{ display: 'grid', gap: '4px' }}>
                {s.per_core_cpu_percent.map((v, i) => (
                  <div key={i} title={`core ${i}: ${fmt(v, 1)}%`} style={{ display: 'grid', gridTemplateColumns: '2.2rem 1fr 3rem', alignItems: 'center', gap: '0.5rem', fontSize: '0.7rem' }}>
                    <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>c{i}</span>
                    <div style={{ height: 8, background: 'rgba(255,255,255,0.06)', borderRadius: 4, overflow: 'hidden' }}>
                      <div style={{ width: `${Math.min(100, v)}%`, height: '100%', background: 'var(--accent-cyan)', borderRadius: 4, transition: 'width 0.4s ease' }} />
                    </div>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', textAlign: 'right' }}>{fmt(v, 0)}%</span>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>Not reported.</div>
            )}
          </div>

          {/* Scale events */}
          <div className="control-card" style={{ padding: '1rem 1.25rem' }}>
            <div className="card-title" style={{ marginBottom: '0.6rem' }}>Scaling events (30 min)</div>
            {data?.scale_events?.length ? (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: '0.35rem', fontSize: '0.76rem' }}>
                {data.scale_events.slice(0, 12).map((e) => (
                  <li key={`${e.type}-${e.node}-${e.at}`} style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem' }}>
                    <span>
                      <strong style={{ color: e.type === 'join' ? 'var(--accent-green)' : 'var(--accent-pink)' }}>
                        {e.type === 'join' ? '▲ joined' : '▼ left'}
                      </strong>{' '}
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{e.node.split('_')[1] || e.node}</span>
                    </span>
                    <span style={{ color: 'var(--text-muted)' }}>{ago(now - e.at)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>No node joins or departures recently.</div>
            )}
          </div>
        </div>
      </div>

      <CommunityModeration user={user} onRequestSignIn={onRequestSignIn} />
    </div>
  );
}
