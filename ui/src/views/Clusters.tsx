import { useState } from 'react'
import {
  useClusters, useCreateCluster, useStartCluster, useLoadImageIntoCluster,
  useClusterKubeconfig, useDeleteCluster,
} from '../api/clusters'
import type { ClusterInfo, ClusterNode, ClusterCreateBody } from '../api/clusters'
import { Box, GlassBox, Modal, Badge } from './shared'

const input: React.CSSProperties = { width: '100%', padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', background: '#0f0f13', color: 'var(--fg)', fontSize: 13, boxSizing: 'border-box' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }
const btnStyle: React.CSSProperties = { padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)', background: '#1c1c22', color: 'var(--fg)', cursor: 'pointer', fontSize: 12 }
const dangerBtn: React.CSSProperties = { ...btnStyle, borderColor: '#ef444440', color: '#ef4444' }
const primaryBtn: React.CSSProperties = { padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }
const th: React.CSSProperties = { padding: '8px 12px', fontSize: 11, textTransform: 'uppercase' }
const td: React.CSSProperties = { padding: '8px 12px' }
const preStyle: React.CSSProperties = { fontSize: 11, background: '#0f0f13', padding: 12, borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0, maxHeight: 500, overflow: 'auto' }

/** running → green, partial → amber, stopped/unknown → muted (SPEC phase 8 §4). */
function statusColor(status: string): string {
  if (status === 'running') return '#22c55e'
  if (status === 'partial') return '#f59e0b'
  return '#71717a'
}

/** Long container ids are truncated in the table; the full id stays in the title. */
function truncate(s: string, n = 20): string {
  return s.length > n ? s.slice(0, n) + '…' : s
}

/** Nodes from either wire shape: `nodes` array, else control_plane + workers. */
function clusterNodes(c: ClusterInfo): ClusterNode[] {
  if (c.nodes && c.nodes.length) return c.nodes
  return [...(c.control_plane ? [c.control_plane] : []), ...(c.workers ?? [])]
}

export default function ClustersView() {
  const { data: clusters, isLoading } = useClusters()
  const [showCreate, setShowCreate] = useState(false)

  return (
    <Box>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Clusters</h2>
          <p style={{ fontSize: 12, color: 'var(--muted-fg)', marginTop: 4 }}>
            Local Kubernetes clusters — a control-plane node plus workers, wrapped over the k8s plugin
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} style={primaryBtn}>+ New</button>
      </div>

      {isLoading && <p style={{ color: 'var(--muted-fg)' }}>Loading...</p>}

      <GlassBox style={{ padding: 0, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--muted-fg)', textAlign: 'left' }}>
              <th style={th}>Name</th>
              <th style={th}>Status</th>
              <th style={th}>Control plane</th>
              <th style={th}>Workers</th>
              <th style={th}>Nodes</th>
              <th style={th}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {!clusters?.length && !isLoading && (
              <tr><td colSpan={6} style={{ padding: 24, textAlign: 'center', color: 'var(--muted-fg)' }}>No clusters. Create one with + New</td></tr>
            )}
            {clusters?.map(c => (
              <ClusterRow key={c.name || c.id} cluster={c} />
            ))}
          </tbody>
        </table>
      </GlassBox>

      {showCreate && (
        <Modal onClose={() => setShowCreate(false)}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>New Cluster</h3>
          <CreateClusterForm onDone={() => setShowCreate(false)} />
        </Modal>
      )}
    </Box>
  )
}

function ClusterRow({ cluster: c }: { cluster: ClusterInfo }) {
  const start = useStartCluster()
  const del = useDeleteCluster()
  const [loadOpen, setLoadOpen] = useState(false)
  const [image, setImage] = useState('')
  const [platform, setPlatform] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [kubeconfigOpen, setKubeconfigOpen] = useState(false)
  const load = useLoadImageIntoCluster()
  const status = c.status || (c.is_running ? 'running' : 'stopped')
  const nodes = clusterNodes(c)
  const controlPlane = c.control_plane ?? nodes.find(n => n.is_control_plane) ?? null
  const workers = c.workers ?? nodes.filter(n => !n.is_control_plane)

  const submitLoad = (e: React.FormEvent) => {
    e.preventDefault()
    if (!image.trim()) return
    setLoadError(null)
    load.mutate({ name: c.name, body: { image: image.trim(), ...(platform.trim() ? { platform: platform.trim() } : {}) } }, {
      onSuccess: () => { setImage(''); setPlatform(''); setLoadOpen(false) },
      onError: err => setLoadError((err as Error).message),
    })
  }

  return (
    <tr style={{ borderBottom: '1px solid var(--border)', verticalAlign: 'top' }}>
      <td style={td}>
        <code style={{ fontSize: 13 }}>{c.name}</code>
      </td>
      <td style={td}>
        <Badge color={statusColor(status)}>{status}</Badge>
      </td>
      <td style={td}>
        {controlPlane ? (
          <>
            <div style={{ fontFamily: 'monospace', fontSize: 12 }} title={controlPlane.id}>{truncate(controlPlane.id)}</div>
            {controlPlane.address && <div style={{ fontSize: 11, color: 'var(--muted-fg)', fontFamily: 'monospace', marginTop: 2 }}>{controlPlane.address}</div>}
          </>
        ) : (
          <span style={{ fontSize: 12, color: 'var(--muted-fg)' }}>—</span>
        )}
      </td>
      <td style={{ ...td, fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{workers.length || '—'}</td>
      <td style={td}>
        {nodes.length ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {nodes.map(n => (
              <div key={n.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
                <span style={{ color: n.is_running ? '#22c55e' : '#71717a', fontSize: 9 }}>{n.is_running ? '●' : '○'}</span>
                <Badge color={n.is_control_plane ? '#3b82f6' : '#71717a'}>{n.is_control_plane ? 'control-plane' : (n.role || 'worker')}</Badge>
                <span style={{ fontFamily: 'monospace', color: 'var(--muted-fg)' }} title={n.id}>{truncate(n.id, 24)}</span>
              </div>
            ))}
          </div>
        ) : (
          <span style={{ fontSize: 12, color: 'var(--muted-fg)' }}>—</span>
        )}
      </td>
      <td style={td}>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {!c.is_running && (
            <button onClick={() => start.mutate(c.name)} style={btnStyle} disabled={start.isPending}>
              {start.isPending ? 'Starting...' : 'Start'}
            </button>
          )}
          <button onClick={() => setLoadOpen(!loadOpen)} style={{ ...btnStyle, color: loadOpen ? '#3b82f6' : 'var(--fg)' }}>Load image</button>
          <button onClick={() => setKubeconfigOpen(true)} style={btnStyle}>Kubeconfig</button>
          <button
            onClick={() => { if (confirm(`Delete cluster ${c.name}? This removes all its node containers.`)) del.mutate(c.name) }}
            style={dangerBtn}
            disabled={del.isPending}
          >Delete</button>
        </div>

        {loadOpen && (
          <form onSubmit={submitLoad} style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <input value={image} onChange={e => setImage(e.target.value)} placeholder="image e.g. nginx:latest" style={{ ...input, flex: 1 }} />
              <input value={platform} onChange={e => setPlatform(e.target.value)} placeholder="platform (optional)" style={{ ...input, flex: 1 }} />
              <button type="submit" disabled={!image.trim() || load.isPending} style={{ ...btnStyle, color: '#22c55e' }}>{load.isPending ? 'Loading...' : 'Load'}</button>
            </div>
            {loadError && <div style={{ fontSize: 11, color: '#ef4444' }}>{loadError}</div>}
          </form>
        )}
        {kubeconfigOpen && <KubeconfigModal name={c.name} onClose={() => setKubeconfigOpen(false)} />}
      </td>
    </tr>
  )
}

function KubeconfigModal({ name, onClose }: { name: string; onClose: () => void }) {
  const { data, isLoading, error } = useClusterKubeconfig(name)
  return (
    <Modal onClose={onClose}>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Kubeconfig · {name}</h3>
      {isLoading && <p style={{ fontSize: 12, color: 'var(--muted-fg)' }}>Fetching kubeconfig...</p>}
      {error && <p style={{ fontSize: 12, color: '#ef4444' }}>{(error as Error).message}</p>}
      {data && <pre style={preStyle}>{data.kubeconfig || '(empty kubeconfig)'}</pre>}
    </Modal>
  )
}

function CreateClusterForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [nodeImage, setNodeImage] = useState('')
  const [cpus, setCpus] = useState('')
  const [memory, setMemory] = useState('')
  const [remove, setRemove] = useState(false)
  const create = useCreateCluster()

  return (
    <form onSubmit={e => {
      e.preventDefault()
      const body: ClusterCreateBody = { name: name.trim() }
      if (nodeImage.trim()) body.node_image = nodeImage.trim()
      const c = parseInt(cpus)
      if (cpus.trim() !== '' && !isNaN(c)) body.cpus = c
      if (memory.trim()) body.memory = memory.trim()
      if (remove) body.remove = true
      create.mutate(body, { onSuccess: onDone })
    }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <label style={label}>Name <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. dev-cluster" style={input} required /></label>
      <label style={label}>Node image <input value={nodeImage} onChange={e => setNodeImage(e.target.value)} placeholder="e.g. lorb:1.3.1 (optional)" style={input} /></label>
      <div style={{ display: 'flex', gap: 12 }}>
        <label style={{ ...label, flex: 1 }}>CPUs <input type="number" value={cpus} onChange={e => setCpus(e.target.value)} placeholder="optional" style={input} /></label>
        <label style={{ ...label, flex: 1 }}>Memory <input value={memory} onChange={e => setMemory(e.target.value)} placeholder="e.g. 2G (optional)" style={input} /></label>
      </div>
      <label style={{ ...label, flexDirection: 'row', alignItems: 'center', gap: 6, width: 'fit-content' }}>
        <input type="checkbox" checked={remove} onChange={e => setRemove(e.target.checked)} />
        Remove node containers when the cluster stops (--rm)
      </label>
      <button type="submit" disabled={!name.trim() || create.isPending} style={primaryBtn}>
        {create.isPending ? 'Creating...' : 'Create cluster'}
      </button>
      {create.isError && <p style={{ color: '#ef4444', fontSize: 12 }}>{(create.error as Error).message}</p>}
    </form>
  )
}
