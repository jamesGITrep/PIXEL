import React, { useState, useEffect, useRef } from 'react';

// Same-origin: /api/* is proxied to the API Gateway. The session is an HttpOnly cookie, so the
// app never sees or stores the token itself.
const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || '';

const TITLES = {
  login: 'Sign in',
  signup: 'Create your account',
  forgot: 'Reset your password',
  reset: 'Choose a new password',
};

async function postJson(path, body) {
  const res = await fetch(`${GATEWAY_URL}/api/v1/auth/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

/**
 * Sign in / sign up / forgot password / reset password, in one dialog.
 * @param {{mode: string, resetToken?: string, onClose: () => void, onSignedIn: (user) => void}} props
 */
export default function AuthDialog({ mode: initialMode, resetToken, onClose, onSignedIn }) {
  const [mode, setMode] = useState(initialMode);
  const [identifier, setIdentifier] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState(null);
  const firstFieldRef = useRef(null);

  useEffect(() => {
    setError('');
    setInfo(null);
    setPassword('');
    setConfirm('');
    firstFieldRef.current?.focus();
  }, [mode]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if ((mode === 'signup' || mode === 'reset') && password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'login') {
        const { user } = await postJson('login', { identifier, password });
        onSignedIn(user);
      } else if (mode === 'signup') {
        const { user } = await postJson('signup', { username, email, password });
        onSignedIn(user);
      } else if (mode === 'forgot') {
        const data = await postJson('forgot-password', { email });
        setInfo({ text: data.message, link: data.reset_link });
      } else if (mode === 'reset') {
        const { user } = await postJson('reset-password', { token: resetToken, new_password: password });
        onSignedIn(user);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const passwordField = (label, value, setter, autoComplete, ref) => (
    <label className="auth-field">
      <span>{label}</span>
      <input ref={ref} type="password" value={value} onChange={(e) => setter(e.target.value)}
        autoComplete={autoComplete} minLength={8} maxLength={72} required />
    </label>
  );

  return (
    <div className="auth-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <button type="button" className="auth-close" onClick={onClose} aria-label="Close">&times;</button>
        <h2 id="auth-title" className="auth-title">{TITLES[mode]}</h2>
        {mode === 'signup' && (
          <p className="auth-sub">Your personal photo vault is saved to your account, so it is there on any device.</p>
        )}

        <form onSubmit={submit} className="auth-form">
          {mode === 'login' && (
            <>
              <label className="auth-field">
                <span>Username or email</span>
                <input ref={firstFieldRef} value={identifier} onChange={(e) => setIdentifier(e.target.value)}
                  autoComplete="username" required />
              </label>
              {passwordField('Password', password, setPassword, 'current-password')}
            </>
          )}

          {mode === 'signup' && (
            <>
              <label className="auth-field">
                <span>Username</span>
                <input ref={firstFieldRef} value={username} onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username" pattern="[A-Za-z0-9_]{3,32}" required
                  title="3-32 letters, digits or underscores" />
              </label>
              <label className="auth-field">
                <span>Email</span>
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
              </label>
              {passwordField('Password (8+ characters)', password, setPassword, 'new-password')}
              {passwordField('Confirm password', confirm, setConfirm, 'new-password')}
            </>
          )}

          {mode === 'forgot' && !info && (
            <label className="auth-field">
              <span>Account email</span>
              <input ref={firstFieldRef} type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                autoComplete="email" required />
            </label>
          )}

          {mode === 'reset' && (
            <>
              {passwordField('New password (8+ characters)', password, setPassword, 'new-password', firstFieldRef)}
              {passwordField('Confirm new password', confirm, setConfirm, 'new-password')}
            </>
          )}

          {error && <div className="auth-error" role="alert">{error}</div>}
          {info && (
            <div className="auth-info" role="status">
              {info.text}
              {info.link && (
                <div style={{ marginTop: '0.5rem' }}>
                  Development mode: <a href={info.link}>open your reset link</a>
                </div>
              )}
            </div>
          )}

          {!(mode === 'forgot' && info) && (
            <button type="submit" className="btn-action-primary btn-cyan-ghost" disabled={busy}>
              {busy ? 'Please wait…' : { login: 'Sign in', signup: 'Create account', forgot: 'Send reset link', reset: 'Save new password' }[mode]}
            </button>
          )}
        </form>

        <div className="auth-links">
          {mode === 'login' && (
            <>
              <button type="button" onClick={() => setMode('forgot')}>Forgot password?</button>
              <button type="button" onClick={() => setMode('signup')}>New here? Create an account</button>
            </>
          )}
          {mode === 'signup' && <button type="button" onClick={() => setMode('login')}>Already have an account? Sign in</button>}
          {(mode === 'forgot' || mode === 'reset') && <button type="button" onClick={() => setMode('login')}>Back to sign in</button>}
        </div>
      </div>
    </div>
  );
}
