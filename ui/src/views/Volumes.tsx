import { useState } from 'react'
import { useVolumes, useCreateVolume, useDeleteVolume, usePruneVolumes } from '../api/volumes'
import type { VolumeInfo, VolumeCreateBody } from '../api/volumes'
import { Box, GlassBox, Modal, Badge } from './shared'

export default function VolumesView() {
  const { data: volumes, isLoading } = useVolumes()
  const [showCreate, setShowCreate] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const remove = useDeleteVolume()
  const prune = usePruneVolumes()

  return (
    <Box>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Volumes</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => {
              if (confirm('Prune unused volumes? Volumes not attached to any container will be deleted permanently.')) {
                prune.mutate(undefined, {
                  onSuccess: res => setNote(
                    res.removed.length
                      ? `Pruned ${res.removed.length} unused volume${res.removed.length === 1 ? '' : 's'}: ${res.removed.join(', ')}`
                      : 'No unused volumes to prune.'
                  ),
                })
              }
            }}
            disabled={prune.isPending}
            style={secondaryBtn}
          >
            {prune.isPending ? 'Pruning...' : 'Prune unused'}
          </button>
          <button onClick={() => setShowCreate(true)} style={primaryBtn}>+ New</button>
        </div>
      </div>

      {note && <p style={{ color: '#22c55e', fontSize: 12, margin: '0 0 12px' }}>{note}</p>}

      {isLoading ? (
        <p style={{ color: 'var(--muted-fg)' }}>Loading...</p>
      ) : (
        <GlassBox style={{ padding: 0, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--muted-fg)', textAlign: 'left' }}>
                <th style={th}>Name</th>
                <th style={th}>Size</th>
                <th style={th}>Driver</th>
                <th style={th}>Format</th>
                <th style={th}>Source</th>
                <th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {!volumes?.length && (
                <tr><td colSpan={6} style={{ padding: 24, textAlign: 'center', color: 'var(--muted-fg)' }}>No volumes found.</td></tr>
              )}
              {volumes?.map(v => (
                <tr key={v.id || v.name} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={td}><code style={{ fontSize: 12 }}>{v.name}</code></td>
                  <td style={{ ...td, fontSize: 12, color: 'var(--muted-fg)', fontVariantNumeric: 'tabular-nums' }}>{sizeLabel(v)}</td>
                  <td style={td}><Badge>{v.driver || '—'}</Badge></td>
                  <td style={{ ...td, fontSize: 12, color: 'var(--muted-fg)' }}>{v.format || '—'}</td>
                  <td style={{ ...td, fontSize: 11, fontFamily: 'monospace', color: 'var(--muted-fg)', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={v.source}>{v.source || '—'}</td>
                  <td style={{ ...td, display: 'flex', gap: 4 }}>
                    <button
                      onClick={() => {
                        if (confirm(`Delete volume ${v.name}? This permanently removes its disk image.`)) {
                          remove.mutate(v.name, { onSuccess: () => setNote(`Deleted volume ${v.name}`) })
                        }
                      }}
                      disabled={remove.isPending}
                      style={dangerBtn}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </GlassBox>
      )}

      {showCreate && (
        <Modal onClose={() => setShowCreate(false)}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>New Volume</h3>
          <CreateVolumeForm onDone={() => setShowCreate(false)} />
        </Modal>
      )}
    </Box>
  )
}

function CreateVolumeForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [size, setSize] = useState('')
  const create = useCreateVolume()

  return (
    <form onSubmit={e => {
      e.preventDefault()
      const body: VolumeCreateBody = { name: name.trim() }
      if (size.trim()) body.size = size.trim()
      create.mutate(body, { onSuccess: onDone })
    }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <label style={label}>Name <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. daedalus-data" style={input} required /></label>
      <label style={label}>Size <input value={size} onChange={e => setSize(e.target.value)} placeholder="e.g. 100M (optional)" style={input} /></label>
      <button type="submit" disabled={!name.trim() || create.isPending} style={primaryBtn}>
        {create.isPending ? 'Creating...' : 'Create volume'}
      </button>
      {create.isError && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{(create.error as Error).message}</p>}
    </form>
  )
}

function sizeLabel(v: VolumeInfo): string {
  const spec = v.size_spec ? ` (${v.size_spec})` : ''
  return v.size_bytes > 0 ? formatBytes(v.size_bytes) + spec : v.size_spec || '—'
}

function formatBytes(b: number): string {
  if (b > 1e12) return (b / 1e12).toFixed(1) + ' TB'
  if (b > 1e9) return (b / 1e9).toFixed(1) + ' GB'
  if (b > 1e6) return (b / 1e6).toFixed(1) + ' MB'
  if (b > 1e3) return (b / 1e3).toFixed(1) + ' KB'
  return b.toLocaleString() + ' B'
}

const primaryBtn: React.CSSProperties = { padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }
const secondaryBtn: React.CSSProperties = { padding: '8px 16px', background: 'transparent', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', cursor: 'pointer', fontSize: 13 }
const th: React.CSSProperties = { padding: '8px 12px', fontSize: 11, textTransform: 'uppercase' }
const td: React.CSSProperties = { padding: '8px 12px' }
const dangerBtn: React.CSSProperties = { padding: '2px 8px', fontSize: 11, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--danger)', cursor: 'pointer' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }
const input: React.CSSProperties = { padding: '8px 12px', background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 13 }
