import { useState } from 'react'
import {
  useRegistryDefault,
  useRegistryLogin,
  useRegistryLogout,
  useRegistryDefaultSet,
  useRegistryDefaultUnset,
} from '../api/system'
import { Box, GlassBox } from './shared'

export default function RegistriesView() {
  const { data: def, isLoading: loadingDef, error: defError } = useRegistryDefault()
  const setDefault = useRegistryDefaultSet()
  const unsetDefault = useRegistryDefaultUnset()
  const login = useRegistryLogin()
  const logout = useRegistryLogout()

  // login form state
  const [loginServer, setLoginServer] = useState('')
  const [loginUsername, setLoginUsername] = useState('')
  const [loginPassword, setLoginPassword] = useState('')

  // logout form state
  const [logoutServer, setLogoutServer] = useState('')

  const currentHost = def?.host?.trim() || ''

  return (
    <Box>
      <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 20 }}>Registries</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        <GlassBox>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--muted-fg)' }}>Default Registry</h3>
          <p style={{ fontSize: 12, color: 'var(--muted-fg)', margin: '0 0 12px' }}>
            {loadingDef
              ? 'Loading...'
              : defError
                ? `Unavailable — ${(defError as Error).message}`
                : currentHost
                  ? <>Default: <code style={{ color: 'var(--fg)' }}>{currentHost}</code></>
                  : 'No default registry configured.'}
          </p>
          <DefaultSetForm onSet={(host, scheme) => setDefault.mutate({ host, scheme })} busy={setDefault.isPending} error={setDefault.error} />
          <button
            onClick={() => { if (confirm('Clear the default registry?')) unsetDefault.mutate() }}
            disabled={unsetDefault.isPending || !currentHost}
            style={{ ...btnStyle, ...secondaryBtn, width: '100%' }}
          >
            {unsetDefault.isPending ? 'Clearing...' : 'Unset default'}
          </button>
          {unsetDefault.error && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{(unsetDefault.error as Error).message}</p>}
        </GlassBox>

        <GlassBox>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--muted-fg)' }}>Registry Login</h3>
          <form
            onSubmit={e => {
              e.preventDefault()
              login.mutate(
                { server: loginServer, username: loginUsername || undefined, password: loginPassword || undefined },
                { onSuccess: () => setLoginPassword('') },
              )
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
          >
            <label style={label}>Server <input value={loginServer} onChange={e => setLoginServer(e.target.value)} placeholder="e.g. docker.io" style={input} required /></label>
            <label style={label}>Username <input value={loginUsername} onChange={e => setLoginUsername(e.target.value)} placeholder="optional" autoComplete="username" style={input} /></label>
            <label style={label}>Password <input type="password" value={loginPassword} onChange={e => setLoginPassword(e.target.value)} placeholder="optional" autoComplete="current-password" style={input} /></label>
            <button type="submit" disabled={login.isPending || !loginServer} style={{ ...btnStyle, width: '100%' }}>
              {login.isPending ? 'Logging in...' : 'Login'}
            </button>
            {login.isSuccess && <p style={{ color: 'var(--success)', fontSize: 12 }}>Logged in to {login.data.server}.</p>}
            {login.isError && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{(login.error as Error).message}</p>}
          </form>

          <div style={{ borderTop: '1px solid var(--border)', margin: '16px 0 12px' }} />
          <form
            onSubmit={e => {
              e.preventDefault()
              logout.mutate(logoutServer)
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
          >
            <label style={label}>Server <input value={logoutServer} onChange={e => setLogoutServer(e.target.value)} placeholder="e.g. docker.io" style={input} required /></label>
            <button type="submit" disabled={logout.isPending || !logoutServer} style={{ ...btnStyle, ...secondaryBtn, width: '100%' }}>
              {logout.isPending ? 'Logging out...' : 'Logout'}
            </button>
            {logout.isSuccess && <p style={{ color: 'var(--success)', fontSize: 12 }}>Logged out of {logout.data.server}.</p>}
            {logout.isError && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{(logout.error as Error).message}</p>}
          </form>
        </GlassBox>
      </div>
    </Box>
  )
}

/** Default-registry set form — local state so the card stays stateless. */
function DefaultSetForm({ onSet, busy, error }: {
  onSet: (host: string, scheme?: string) => void
  busy: boolean
  error: Error | null
}) {
  const [host, setHost] = useState('')
  const [scheme, setScheme] = useState('')
  return (
    <form
      onSubmit={e => {
        e.preventDefault()
        onSet(host.trim(), scheme.trim() || undefined)
        setHost('')
        setScheme('')
      }}
      style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 12 }}
    >
      <label style={label}>Host <input value={host} onChange={e => setHost(e.target.value)} placeholder="e.g. docker.io" style={input} required /></label>
      <label style={label}>Scheme <input value={scheme} onChange={e => setScheme(e.target.value)} placeholder="http or https (optional)" style={input} /></label>
      <button type="submit" disabled={busy || !host.trim()} style={{ ...btnStyle, ...secondaryBtn, width: '100%' }}>
        {busy ? 'Setting...' : 'Set default'}
      </button>
      {error && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{error.message}</p>}
    </form>
  )
}

const btnStyle: React.CSSProperties = { padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }
const secondaryBtn: React.CSSProperties = { background: '#1c1c22', border: '1px solid var(--border)', color: 'var(--fg)' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }
const input: React.CSSProperties = { padding: '8px 12px', background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 13 }
