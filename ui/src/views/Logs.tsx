import { useEffect, useRef, useState } from 'react'
import { useContainers } from '../api/containers'
import { API_BASE, apiGet } from '../api/client'
import { Box } from './shared'

export default function LogsView() {
  const { data: containers } = useContainers(true)
  const [selectedId, setSelectedId] = useState('')
  const [boot, setBoot] = useState(false)
  const [live, setLive] = useState(false)
  const [lines, setLines] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const seenRef = useRef(new Set<string>())

  const items = containers ?? []

  async function fetchLogs(id: string) {
    if (!id) return
    setLoading(true)
    setError('')
    seenRef.current = new Set()
    setLines([])
    try {
      const data = await apiGet<{ logs: string }>(`/containers/${id}/logs?boot=${boot}`)
      const next = (data.logs || '').split('\n')
      next.forEach(l => seenRef.current.add(l))
      setLines(next)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!live || !selectedId) return

    const es = new EventSource(`${API_BASE}/containers/${selectedId}/logs/stream?boot=${boot}`)

    es.onmessage = (ev) => {
      const line = ev.data
      if (!line || seenRef.current.has(line)) return
      seenRef.current.add(line)
      setLines(prev => [...prev, line])
    }

    es.addEventListener('error', () => {
      setError('Log stream interrupted')
      es.close()
      setLive(false)
    })

    return () => es.close()
  }, [live, selectedId, boot])

  return (
    <Box>
      <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 20 }}>Logs</h2>
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          value={selectedId}
          onChange={e => {
            setSelectedId(e.target.value)
            setLive(false)
            if (e.target.value) fetchLogs(e.target.value)
          }}
          style={select}
        >
          <option value="">Select a container...</option>
          {items.map(c => (
            <option key={c.id} value={c.id}>
              {c.id.slice(0, 12)} ({c.state}) — {c.image.replace('docker.io/library/', '')}
            </option>
          ))}
        </select>
        <label style={checkLabel}>
          <input type="checkbox" checked={boot} onChange={e => {
            setBoot(e.target.checked)
            setLive(false)
            if (selectedId) fetchLogs(selectedId)
          }} /> Boot logs
        </label>
        <label style={checkLabel}>
          <input type="checkbox" checked={live} disabled={!selectedId}
            onChange={e => {
              if (e.target.checked) {
                setLines(prev => { prev.forEach(l => seenRef.current.add(l)); return prev })
              }
              setLive(e.target.checked)
            }} /> Live tail
        </label>
        {selectedId && (
          <button onClick={() => { setLive(false); fetchLogs(selectedId) }} style={refreshBtn}>
            Refresh
          </button>
        )}
      </div>
      {error && <p style={{ color: 'var(--danger)', fontSize: 13, marginBottom: 12 }}>{error}</p>}
      {loading && <p style={{ color: 'var(--muted-fg)' }}>Loading...</p>}
      {!loading && selectedId && (
        <div style={logBox}>
          {lines.length === 0 ? (
            <span style={{ color: 'var(--muted-fg)' }}>
              No output yet — the workload may not have written to stdout/stderr.
            </span>
          ) : lines.map((l, i) => (
            <div key={i}>{l || '\u00a0'}</div>
          ))}
        </div>
      )}
      {!selectedId && <p style={{ color: 'var(--muted-fg)' }}>Select a container to view logs.</p>}
    </Box>
  )
}

const select: React.CSSProperties = { padding: '8px 12px', background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 13, minWidth: 280 }
const checkLabel: React.CSSProperties = { fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, color: 'var(--muted-fg)' }
const refreshBtn: React.CSSProperties = { padding: '6px 12px', background: '#1c1c22', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 12, cursor: 'pointer' }
const logBox: React.CSSProperties = { background: '#0a0a0e', border: '1px solid var(--border)', borderRadius: 8, padding: 12, fontFamily: 'monospace', fontSize: 12, maxHeight: 'calc(100vh - 250px)', overflow: 'auto', whiteSpace: 'pre-wrap' }
