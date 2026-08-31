import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
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

  // Split view: up to 4 container ids shown as colored panes.
  const [paneIds, setPaneIds] = useState<string[]>([])

  function togglePane(id: string) {
    setPaneIds(prev => {
      if (prev.includes(id)) return prev.filter(x => x !== id)
      if (prev.length >= 4) return prev
      return [...prev, id]
    })
  }

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

      <div style={{ marginTop: 28 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--muted-fg)' }}>Split View</h3>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
          {items.length === 0 && <span style={{ color: 'var(--muted-fg)', fontSize: 12 }}>No containers found.</span>}
          {items.map(c => (
            <label key={c.id} style={checkLabel}>
              <input
                type="checkbox"
                checked={paneIds.includes(c.id)}
                disabled={!paneIds.includes(c.id) && paneIds.length >= 4}
                onChange={() => togglePane(c.id)}
              />
              {c.id.slice(0, 12)} ({c.state}) — {c.image.replace('docker.io/library/', '')}
            </label>
          ))}
        </div>
        {paneIds.length < 2 ? (
          <p style={{ color: 'var(--muted-fg)', fontSize: 12 }}>Select 2–4 containers to view them side-by-side.</p>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: paneIds.length === 4 ? 'repeat(2, 1fr)' : `repeat(${paneIds.length}, 1fr)`, gap: 12 }}>
            {paneIds.map((id, i) => {
              const c = items.find(x => x.id === id)
              return (
                <LogPane
                  key={id}
                  containerId={id}
                  label={c ? `${c.id.slice(0, 12)} — ${c.image.replace('docker.io/library/', '')}` : id}
                  color={PANE_COLORS[i % PANE_COLORS.length]}
                />
              )
            })}
          </div>
        )}
      </div>
    </Box>
  )
}

/** Accent cycle for split panes — pane i takes PANE_COLORS[i % len]. */
const PANE_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#a855f7']

/**
 * One split-view pane: snapshot tail of one container refreshed every 5 s
 * (per-pane refresh), with an optional follow toggle that switches to the
 * SSE stream and appends fresh lines.  The snapshot is a useQuery so the
 * 5 s cadence is managed by react-query, not a hand-rolled timer.
 */
function LogPane({ containerId, label, color }: { containerId: string; label: string; color: string }) {
  const [lines, setLines] = useState<string[]>([])
  const [follow, setFollow] = useState(false)
  const [streamError, setStreamError] = useState('')
  const seenRef = useRef(new Set<string>())
  const linesRef = useRef<string[]>([])

  const snap = useQuery({
    queryKey: ['pane-logs', containerId],
    queryFn: () => apiGet<{ logs: string }>(`/containers/${containerId}/logs?boot=false`),
    refetchInterval: 5000,
    enabled: !follow,
  })

  useEffect(() => { linesRef.current = lines }, [lines])

  // Snapshot refresh replaces the pane wholesale; follow owns the lines.
  useEffect(() => {
    if (follow || !snap.data) return
    const next = (snap.data.logs || '').split('\n')
    seenRef.current = new Set(next)
    setLines(next)
  }, [snap.data, follow])

  // Follow: seed dedupe with what's shown, then append streamed lines.
  useEffect(() => {
    if (!follow) return
    setStreamError('')
    linesRef.current.forEach(l => seenRef.current.add(l))
    const es = new EventSource(`${API_BASE}/containers/${containerId}/logs/stream?boot=false`)

    es.onmessage = ev => {
      const line = ev.data
      if (!line || seenRef.current.has(line)) return
      seenRef.current.add(line)
      setLines(prev => [...prev, line])
    }

    es.addEventListener('error', () => {
      setStreamError('Log stream interrupted')
      es.close()
      setFollow(false)
    })

    return () => es.close()
  }, [follow, containerId])

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, background: '#0a0a0e', overflow: 'hidden', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '6px 10px', borderBottom: `2px solid ${color}` }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
        <label style={{ ...checkLabel, flexShrink: 0 }}>
          <input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} /> follow
        </label>
      </div>
      <div style={{ fontFamily: 'monospace', fontSize: 11, padding: 8, overflow: 'auto', flex: 1, maxHeight: 320, whiteSpace: 'pre-wrap' }}>
        {streamError && <span style={{ color: 'var(--danger)' }}>{streamError}</span>}
        {!streamError && lines.length === 0 && <span style={{ color: 'var(--muted-fg)' }}>No output yet.</span>}
        {lines.map((l, i) => (
          <div key={i}>{l || ' '}</div>
        ))}
      </div>
    </div>
  )
}

const select: React.CSSProperties = { padding: '8px 12px', background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 13, minWidth: 280 }
const checkLabel: React.CSSProperties = { fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, color: 'var(--muted-fg)' }
const refreshBtn: React.CSSProperties = { padding: '6px 12px', background: '#1c1c22', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 12, cursor: 'pointer' }
const logBox: React.CSSProperties = { background: '#0a0a0e', border: '1px solid var(--border)', borderRadius: 8, padding: 12, fontFamily: 'monospace', fontSize: 12, maxHeight: 'calc(100vh - 250px)', overflow: 'auto', whiteSpace: 'pre-wrap' }
