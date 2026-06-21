import { useState } from 'react'
import './Auth.css'
import { API } from '../api'

const PASSWORD_RULES = [
  { label: '8–45 characters',       test: p => p.length >= 8 && p.length <= 45 },
  { label: 'One uppercase letter',  test: p => /[A-Z]/.test(p) },
  { label: 'One lowercase letter',  test: p => /[a-z]/.test(p) },
  { label: 'One number',            test: p => /[0-9]/.test(p) },
]

export default function Auth({ onAuth }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const isRegister = mode === 'register'
  const passwordTouched = isRegister && password.length > 0

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')

    if (isRegister) {
      if (PASSWORD_RULES.some(r => !r.test(password))) {
        setError('Password does not meet all requirements')
        return
      }
      if (password !== confirm) {
        setError('Passwords do not match')
        return
      }
    }

    setLoading(true)
    try {
      const endpoint = isRegister ? `${API}/api/auth/register` : `${API}/api/auth/login`
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error || 'Something went wrong')
        return
      }
      onAuth({ username: data.username, token: data.access_token, isGuest: false })
    } catch {
      setError('Could not reach server')
    } finally {
      setLoading(false)
    }
  }

  function switchMode(next) {
    setMode(next)
    setError('')
    setConfirm('')
  }

  return (
    <div className="auth-backdrop">
      <div className="auth-card">
        <h1 className="auth-title">DataSearch</h1>

        <div className="auth-tabs">
          <button
            className={`auth-tab${mode === 'login' ? ' auth-tab-active' : ''}`}
            onClick={() => switchMode('login')}
          >Sign in</button>
          <button
            className={`auth-tab${mode === 'register' ? ' auth-tab-active' : ''}`}
            onClick={() => switchMode('register')}
          >Register</button>
        </div>

        <form onSubmit={handleSubmit} className="auth-form">
          <input
            type="text"
            placeholder="Username"
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoComplete={isRegister ? 'new-password' : 'current-password'}
            maxLength={55}
            required
          />

          {passwordTouched && (
            <ul className="auth-rules">
              {PASSWORD_RULES.map(r => (
                <li key={r.label} className={r.test(password) ? 'auth-rule-pass' : 'auth-rule-fail'}>
                  {r.test(password) ? '✓' : '✗'} {r.label}
                </li>
              ))}
            </ul>
          )}

          {isRegister && (
            <input
              type="password"
              placeholder="Confirm password"
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
              autoComplete="off"
              maxLength={55}
              required
            />
          )}

          {error && <p className="auth-error">{error}</p>}
          <button type="submit" className="auth-submit" disabled={loading}>
            {loading ? '…' : isRegister ? 'Create account' : 'Sign in'}
          </button>
        </form>

        <button className="auth-guest" onClick={() => onAuth({ username: 'Guest', isGuest: true })}>
          Continue as guest
        </button>
      </div>
    </div>
  )
}
