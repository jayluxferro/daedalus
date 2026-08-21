import { useState } from 'react'
import { useNetworks, useCreateNetwork, useDeleteNetwork } from '../api/system'
import type { NetworkCreateBody } from '../api/system'
import { Box, GlassBox, Modal, Badge } from './shared'

export default function NetworksView() {
  const { data: networks, isLoading } = useNetworks()
  const [showCreate, setShowCreate] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const remove = useDeleteNetwork()

  return (
    <Box>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Networks</h2>
        <button onClick={() => setShowCreate(true)} style={primaryBtn}>+ New</button>
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
                <th style={th}>Subnet</th>
                <th style={th}>Subnet V6</th>
                <th style={th}>Gateway</th>
                <th style={th}>Plugin</th>
                <th style={th}>Mode</th>
                <th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {!networks?.length && (
                <tr><td colSpan={7} style={{ padding: 24, textAlign: 'center', color: 'var(--muted-fg)' }}>No networks found.</td></tr>
              )}
              {networks?.map(n => (
                <tr key={n.id || n.name} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={td}><code style={{ fontSize: 12 }}>{n.name}</code></td>
                  <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>{n.subnet || '—'}</td>
                  <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>{n.subnet_v6 || '—'}</td>
                  <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>{n.gateway || '—'}</td>
                  <td style={{ ...td, fontSize: 12, color: 'var(--muted-fg)' }}>{n.plugin || '—'}</td>
                  <td style={td}><Badge>{n.mode || '—'}</Badge></td>
                  <td style={{ ...td, display: 'flex', gap: 4 }}>
                    <button
                      onClick={() => {
                        if (confirm(`Delete network ${n.name}?`)) {
                          remove.mutate(n.name, { onSuccess: () => setNote(`Deleted network ${n.name}`) })
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
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>New Network</h3>
          <CreateNetworkForm onDone={() => setShowCreate(false)} />
        </Modal>
      )}
    </Box>
  )
}

function CreateNetworkForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [subnet, setSubnet] = useState('')
  const [subnetV6, setSubnetV6] = useState('')
  const [internal, setInternal] = useState(false)
  const create = useCreateNetwork()

  return (
    <form onSubmit={e => {
      e.preventDefault()
      const body: NetworkCreateBody = { name: name.trim(), internal }
      if (subnet.trim()) body.subnet = subnet.trim()
      if (subnetV6.trim()) body.subnet_v6 = subnetV6.trim()
      create.mutate(body, { onSuccess: onDone })
    }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <label style={label}>Name <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. lab-net" style={input} required /></label>
      <label style={label}>Subnet <input value={subnet} onChange={e => setSubnet(e.target.value)} placeholder="e.g. 10.0.0.0/24 (optional)" style={input} /></label>
      <label style={label}>Subnet v6 <input value={subnetV6} onChange={e => setSubnetV6(e.target.value)} placeholder="optional" style={input} /></label>
      <label style={{ ...label, flexDirection: 'row', alignItems: 'center', gap: 6, width: 'fit-content' }}>
        <input type="checkbox" checked={internal} onChange={e => setInternal(e.target.checked)} />
        Internal (no host routing)
      </label>
      <button type="submit" disabled={!name.trim() || create.isPending} style={primaryBtn}>
        {create.isPending ? 'Creating...' : 'Create network'}
      </button>
      {create.isError && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{(create.error as Error).message}</p>}
    </form>
  )
}

const primaryBtn: React.CSSProperties = { padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }
const th: React.CSSProperties = { padding: '8px 12px', fontSize: 11, textTransform: 'uppercase' }
const td: React.CSSProperties = { padding: '8px 12px' }
const dangerBtn: React.CSSProperties = { padding: '2px 8px', fontSize: 11, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--danger)', cursor: 'pointer' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }
const input: React.CSSProperties = { padding: '8px 12px', background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 13 }
