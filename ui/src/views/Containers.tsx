import { useState } from 'react'
import { useContainers, useRunContainer, useStartContainer, useStopContainer, useDestroyContainer, useKillContainer, useInspectContainer } from '../api/containers'
import { useProfiles } from '../api/system'
import { Box, GlassBox, Badge, StatCard, Modal } from './shared'

const input: React.CSSProperties = { width: '100%', padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', background: '#0f0f13', color: 'var(--fg)', fontSize: 13, boxSizing: 'border-box' }
const selectStyle: React.CSSProperties = { ...input, appearance: 'none', WebkitAppearance: 'none', backgroundImage: 'url("data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2712%27 height=%2712%27 viewBox=%270 0 12 12%27%3E%3Cpath fill=%27%2371717a%27 d=%27M3 4.5l3 3 3-3%27/%3E%3C/svg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center', paddingRight: 28 }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }
const btnStyle: React.CSSProperties = { padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)', background: '#1c1c22', color: 'var(--fg)', cursor: 'pointer', fontSize: 12 }
const dangerBtn: React.CSSProperties = { ...btnStyle, borderColor: '#ef444440', color: '#ef4444' }

export default function ContainersView({ onOpenTerminal }: { onOpenTerminal?: (id: string) => void }) {
  const { data: containers, isLoading } = useContainers(true)
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [inspectId, setInspectId] = useState<string | null>(null)

  const items = (containers || [])
    .filter(c => {
      if (filter === 'running') return c.state === 'running'
      if (filter === 'stopped') return c.state !== 'running'
      return true
    })
    .filter(c => {
      if (!search) return true
      const q = search.toLowerCase()
      return c.id.includes(q) || c.name.toLowerCase().includes(q) || c.image.toLowerCase().includes(q)
    })

  const running = (containers || []).filter(c => c.state === 'running').length

  return (
    <Box>
      <div style={{ display: 'flex', gap: 16, marginBottom: 16 }}>
        <StatCard label="Total" value={String(containers?.length || 0)} />
        <StatCard label="Running" value={String(running)} color="#22c55e" />
        <StatCard label="Stopped" value={String((containers?.length || 0) - running)} color="#71717a" />
      </div>

      <GlassBox style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <input placeholder="Search containers..." value={search} onChange={e => setSearch(e.target.value)}
            style={{ ...input, maxWidth: 300 }} />
          {(['all', 'running', 'stopped'] as const).map(s => (
            <button key={s} onClick={() => setFilter(s)} style={{
              ...btnStyle, background: filter === s ? '#3b82f620' : btnStyle.background,
              borderColor: filter === s ? '#3b82f6' : 'var(--border)',
              color: filter === s ? '#3b82f6' : 'var(--fg)',
              textTransform: 'capitalize',
            }}>{s}</button>
          ))}
          <div style={{ flex: 1 }} />
          <button onClick={() => setShowCreate(true)} style={{
            background: '#3b82f6', color: 'white', border: 'none', padding: '8px 16px', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 600,
          }}>+ New</button>
        </div>
      </GlassBox>

      {isLoading && <p style={{ color: 'var(--muted-fg)' }}>Loading...</p>}

      <GlassBox>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border)' }}>
              <th style={{ padding: 8, fontSize: 11, color: 'var(--muted-fg)', fontWeight: 500 }}>ID</th>
              <th style={{ padding: 8, fontSize: 11, color: 'var(--muted-fg)', fontWeight: 500 }}>Name</th>
              <th style={{ padding: 8, fontSize: 11, color: 'var(--muted-fg)', fontWeight: 500 }}>Image</th>
              <th style={{ padding: 8, fontSize: 11, color: 'var(--muted-fg)', fontWeight: 500 }}>State</th>
              <th style={{ padding: 8, fontSize: 11, color: 'var(--muted-fg)', fontWeight: 500 }}>IP</th>
              <th style={{ padding: 8, fontSize: 11, color: 'var(--muted-fg)', fontWeight: 500 }}>Profile</th>
              <th style={{ padding: 8, fontSize: 11, color: 'var(--muted-fg)', fontWeight: 500 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr><td colSpan={7} style={{ padding: 24, textAlign: 'center', color: 'var(--muted-fg)' }}>No containers. Create one with + New</td></tr>
            )}
            {items.map(c => (
              <ContainerRow key={c.id} container={c} onInspect={setInspectId} onOpenTerminal={onOpenTerminal} />
            ))}
          </tbody>
        </table>
      </GlassBox>

      {inspectId && <InspectModal id={inspectId} onClose={() => setInspectId(null)} />}
      {showCreate && (
        <Modal onClose={() => setShowCreate(false)}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>New Container</h3>
          <CreateForm onDone={() => setShowCreate(false)} />
        </Modal>
      )}
    </Box>
  )
}

function ContainerRow({ container: c, onInspect, onOpenTerminal }: {
  container: { id: string; name: string; image: string; state: string; profile: string; ip?: string | null }
  onInspect: (id: string) => void
  onOpenTerminal?: (id: string) => void
}) {
  const start = useStartContainer()
  const stop = useStopContainer()
  const destroy = useDestroyContainer()
  const kill = useKillContainer()
  const running = c.state === 'running'

  const copyIp = () => {
    if (c.ip) navigator.clipboard.writeText(`ssh root@${c.ip}`)
  }

  return (
    <tr style={{ borderBottom: '1px solid var(--border)' }}>
      <td style={{ padding: 8, fontFamily: 'monospace', fontSize: 12 }}>{c.id.slice(0, 12)}</td>
      <td style={{ padding: 8, fontSize: 13 }}>{c.name || '-'}</td>
      <td style={{ padding: 8, fontSize: 12, color: 'var(--muted-fg)' }}>{c.image.replace('docker.io/library/', '')}</td>
      <td style={{ padding: 8 }}>
        <Badge color={running ? '#22c55e' : '#71717a'}>{c.state}</Badge>
      </td>
      <td style={{ padding: 8, fontFamily: 'monospace', fontSize: 11, color: 'var(--muted-fg)' }}>
        {c.ip ? (
          <button onClick={copyIp} title="Copy ssh command" style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', fontFamily: 'inherit', fontSize: 'inherit', padding: 0 }}>
            {c.ip}
          </button>
        ) : '—'}
      </td>
      <td style={{ padding: 8, fontSize: 12 }}>{c.profile}</td>
      <td style={{ padding: 8, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        <button onClick={() => onInspect(c.id)} style={btnStyle}>Inspect</button>
        {onOpenTerminal && running && <button onClick={() => onOpenTerminal(c.id)} style={btnStyle}>Term</button>}
        {!running && <button onClick={() => start.mutate(c.id)} style={{...btnStyle, color:'#22c55e'}} disabled={start.isPending}>Start</button>}
        {running && <button onClick={() => stop.mutate(c.id)} style={btnStyle} disabled={stop.isPending}>Stop</button>}
        {running && <button onClick={() => kill.mutate({ id: c.id })} style={{...btnStyle, color:'#ef4444'}} disabled={kill.isPending}>Kill</button>}
        <button onClick={() => { if (confirm('Destroy container?')) destroy.mutate(c.id) }} style={dangerBtn} disabled={destroy.isPending}>✕</button>
      </td>
    </tr>
  )
}

function InspectModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { data } = useInspectContainer(id)
  return (
    <Modal onClose={onClose}>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Inspect {id.slice(0, 12)}</h3>
      <pre style={{ fontSize: 12, maxHeight: 500, overflow: 'auto', background: '#0f0f13', padding: 12, borderRadius: 8, whiteSpace: 'pre-wrap' }}>
        {data ? JSON.stringify(data, null, 2) : 'Loading...'}
      </pre>
    </Modal>
  )
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const [image, setImage] = useState('alpine:latest')
  const [name, setName] = useState('')
  const [profile, setProfile] = useState('detonation')
  const [detach, setDetach] = useState(true)
  const [command, setCommand] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [kernel, setKernel] = useState('')
  const [cpus, setCpus] = useState('')
  const [memory, setMemory] = useState('')
  const [dns, setDns] = useState('')
  const [volumes, setVolumes] = useState('')
  const [workdir, setWorkdir] = useState('')
  const [hostname, setHostname] = useState('')
  const [env, setEnv] = useState('')
  const run = useRunContainer()
  const { data: profileList } = useProfiles()

  const profiles = profileList?.map(p => p.name) ?? ['general', 'detonation', 'bench', 'fuzz', 'isolated', 'deception']

  return (
    <form onSubmit={e => {
      e.preventDefault()
      // Detached + no command = container exits instantly. Default to sleep.
      const cmd = command ? command.split(' ') : (detach ? ['sleep', '3600'] : undefined)
      const body: Record<string, unknown> = { image, name: name || undefined, profile, detach, command: cmd }
      if (advanced) {
        if (kernel) {
          body.kernel = kernel
          body.confirm_kernel = true
        }
        if (cpus) body.cpus = parseInt(cpus)
        if (memory) body.memory = memory
        if (dns) body.dns = [dns]
        if (volumes) body.volumes = volumes.split(',').map(v => v.trim()).filter(Boolean)
        if (workdir) body.workdir = workdir
        if (hostname) body.hostname = hostname
        if (env) {
          const envObj: Record<string, string> = {}
          for (const pair of env.split(',')) {
            const [k, ...rest] = pair.split('=')
            if (k && rest.length) envObj[k.trim()] = rest.join('=').trim()
          }
          if (Object.keys(envObj).length) body.env = envObj
        }
      }
      run.mutate(body as Parameters<ReturnType<typeof useRunContainer>['mutate']>[0], { onSuccess: onDone })
    }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <label style={label}>Image <input value={image} onChange={e => setImage(e.target.value)} style={input} /></label>
      <div style={{ display: 'flex', gap: 12 }}>
        <label style={{ ...label, flex: 1 }}>Name <input value={name} onChange={e => setName(e.target.value)} placeholder="auto-generated" style={input} /></label>
        <label style={{ ...label, flex: 1 }}>Profile <select value={profile} onChange={e => setProfile(e.target.value)} style={selectStyle}>
          {profiles.map(p => <option key={p} value={p}>{p}</option>)}
        </select></label>
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
        <label style={{ ...label, flex: 1 }}>Command <input value={command} onChange={e => setCommand(e.target.value)} placeholder="e.g. sleep 60" style={input} /></label>
        <label style={{ ...label, flex: 0, flexDirection: 'row', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={detach} onChange={e => setDetach(e.target.checked)} />
          Detach
        </label>
      </div>
      <button type="button" onClick={() => setAdvanced(!advanced)} style={{ ...btnStyle, background: 'transparent', textAlign: 'left', fontSize: 12, color: '#3b82f6', border: 'none' }}>
        {advanced ? '▾ Hide advanced' : '▸ Advanced options'}
      </button>
      {advanced && (
        <GlassBox>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={label}>Kernel <input value={kernel} onChange={e => setKernel(e.target.value)} placeholder="kernel path" style={input} /></label>
            <label style={label}>CPUs <input value={cpus} onChange={e => setCpus(e.target.value)} placeholder="e.g. 2" style={input} /></label>
            <label style={label}>Memory <input value={memory} onChange={e => setMemory(e.target.value)} placeholder="e.g. 512M" style={input} /></label>
            <label style={label}>DNS <input value={dns} onChange={e => setDns(e.target.value)} placeholder="e.g. 8.8.8.8" style={input} /></label>
            <label style={{ ...label, gridColumn: '1 / -1' }}>Volumes <input value={volumes} onChange={e => setVolumes(e.target.value)} placeholder="/host/path:/container/path (comma-separated)" style={input} /></label>
            <label style={label}>Workdir <input value={workdir} onChange={e => setWorkdir(e.target.value)} placeholder="/app" style={input} /></label>
            <label style={label}>Hostname <input value={hostname} onChange={e => setHostname(e.target.value)} placeholder="mybox" style={input} /></label>
            <label style={{ ...label, gridColumn: '1 / -1' }}>Env <input value={env} onChange={e => setEnv(e.target.value)} placeholder="FOO=bar,BAR=baz" style={input} /></label>
          </div>
        </GlassBox>
      )}
      <button type="submit" disabled={run.isPending} style={{ background: '#3b82f6', color: 'white', border: 'none', padding: '10px 16px', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 600, marginTop: 8 }}>
        {run.isPending ? 'Creating...' : 'Create & Run'}
      </button>
      {run.isError && <p style={{ color: '#ef4444', fontSize: 12 }}>{(run.error as Error).message}</p>}
    </form>
  )
}
