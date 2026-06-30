import { Fragment, useCallback, useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import { Box, GlassBox } from './shared'

interface AuditEntry {
  operation: string; actor: string; actor_kind: string;
  args: Record<string, unknown>; result: Record<string, unknown>;
  error: string | null; timestamp: string; entry_id: string;
}

export default function AuditView() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [operation, setOperation] = useState('')
  const [actor, setActor] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({ limit: '200' })
      if (operation) params.set('operation', operation)
      if (actor) params.set('actor', actor)
      setEntries(await apiGet<AuditEntry[]>(`/system/audit?${params}`))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [operation, actor])

  useEffect(() => { load() }, [load])

  return (
    <Box>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Audit Log</h2>
        <button onClick={load} style={btnStyle}>{loading ? 'Loading...' : 'Refresh'}</button>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        <input placeholder="Filter operation..." value={operation} onChange={e => setOperation(e.target.value)}
          style={filterInput} />
        <input placeholder="Filter actor..." value={actor} onChange={e => setActor(e.target.value)}
          style={filterInput} />
      </div>

      {error && <p style={{ color: 'var(--danger)', fontSize: 13, marginBottom: 12 }}>{error}</p>}

      {!loading && entries.length === 0 ? (
        <p style={{ color: 'var(--muted-fg)' }}>No audit entries match filters.</p>
      ) : (
        <GlassBox style={{ padding: 0, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--muted-fg)', textAlign: 'left' }}>
                <th style={th}>Timestamp</th><th style={th}>Operation</th><th style={th}>Actor</th><th style={th}>Kind</th><th style={th}>Result</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(e => (
                <Fragment key={e.entry_id}>
                  <tr style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
                    onClick={() => setExpanded(expanded === e.entry_id ? null : e.entry_id)}>
                    <td style={td}>{new Date(e.timestamp).toLocaleTimeString()}</td>
                    <td style={td}>{e.operation}</td>
                    <td style={td}>{e.actor}</td>
                    <td style={td}>{e.actor_kind}</td>
                    <td style={td}>{e.error ? <span style={{ color: 'var(--danger)' }}>✗ {e.error}</span> : <span style={{ color: 'var(--success)' }}>✓</span>}</td>
                  </tr>
                  {expanded === e.entry_id && (
                    <tr>
                      <td colSpan={5} style={{ padding: '8px 12px', background: '#0f0f13' }}>
                        <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>
                          args: {JSON.stringify(e.args, null, 2)}
                          {'\n'}result: {JSON.stringify(e.result, null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </GlassBox>
      )}
    </Box>
  )
}

const btnStyle: React.CSSProperties = { padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }
const filterInput: React.CSSProperties = { padding: '6px 10px', background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 12, minWidth: 160 }
const th: React.CSSProperties = { padding: '8px 12px', fontSize: 10, textTransform: 'uppercase' }
const td: React.CSSProperties = { padding: '6px 12px' }
