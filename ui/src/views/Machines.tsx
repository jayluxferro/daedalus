import { useState } from 'react'
import {
  useMachines, useCreateMachine, useInspectMachine, useSetMachine,
  useSetMachineDefault, useStopMachine, useDeleteMachine, useRunMachine,
  useMachineLogs,
} from '../api/machines'
import type { MachineInfo, MachineCreateBody, MachineSetBody, MachineRunResult, MachineRunBody } from '../api/machines'
import { Box, GlassBox, Badge, Modal } from './shared'

const input: React.CSSProperties = { width: '100%', padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', background: '#0f0f13', color: 'var(--fg)', fontSize: 13, boxSizing: 'border-box' }
const selectStyle: React.CSSProperties = { ...input, appearance: 'none', WebkitAppearance: 'none', backgroundImage: 'url("data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2712%27 height=%2712%27 viewBox=%270 0 12 12%27%3E%3Cpath fill=%27%2371717a%27 d=%27M3 4.5l3 3 3-3%27/%3E%3C/svg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center', paddingRight: 28 }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }
const btnStyle: React.CSSProperties = { padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)', background: '#1c1c22', color: 'var(--fg)', cursor: 'pointer', fontSize: 12 }
const dangerBtn: React.CSSProperties = { ...btnStyle, borderColor: '#ef444440', color: '#ef4444' }
const primaryBtn: React.CSSProperties = { padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }
const th: React.CSSProperties = { padding: '8px 12px', fontSize: 11, textTransform: 'uppercase' }
const td: React.CSSProperties = { padding: '8px 12px' }
const preStyle: React.CSSProperties = { fontSize: 11, background: '#0f0f13', padding: 8, borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0, maxHeight: 200, overflow: 'auto' }

interface RunOptionField {
  key: string
  label: string
  kind: 'text' | 'bool' | 'repeat'
  placeholder?: string
}

// Machine-run option registry (SPEC phase 7 §7: root, detach, tty, user,
// workdir, ulimit).  Data-driven: renders the run-box controls and folds
// values into the run request body.
const RUN_OPTION_FIELDS: RunOptionField[] = [
  { key: 'root', label: 'Root', kind: 'bool' },
  { key: 'tty', label: 'TTY', kind: 'bool' },
  { key: 'detached', label: 'Detach', kind: 'bool' },
  { key: 'user', label: 'User', kind: 'text', placeholder: 'e.g. root' },
  { key: 'workdir', label: 'Workdir', kind: 'text', placeholder: 'e.g. /app' },
  { key: 'ulimit', label: 'Ulimits', kind: 'repeat', placeholder: 'nofile=1024 (comma/newline separated)' },
]

/** Fold registry values into a machine-run body; empty values are omitted. */
function foldRunFields(vals: Record<string, string | boolean>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const f of RUN_OPTION_FIELDS) {
    const v = vals[f.key]
    if (f.kind === 'bool') {
      if (v === true) out[f.key] = true
    } else if (f.kind === 'repeat') {
      const list = String(v ?? '').split(/[,\n]/).map(s => s.trim()).filter(Boolean)
      if (list.length) out[f.key] = list
    } else {
      const s = String(v ?? '').trim()
      if (s) out[f.key] = s
    }
  }
  return out
}

function formatBytes(b: number): string {
  if (b > 1e12) return (b / 1e12).toFixed(1) + ' TB'
  if (b > 1e9) return (b / 1e9).toFixed(1) + ' GB'
  if (b > 1e6) return (b / 1e6).toFixed(1) + ' MB'
  return b.toLocaleString() + ' B'
}

export default function MachinesView() {
  const { data: machines, isLoading } = useMachines()
  const [showCreate, setShowCreate] = useState(false)
  const [inspectId, setInspectId] = useState<string | null>(null)

  return (
    <Box>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Machines</h2>
          <p style={{ fontSize: 12, color: 'var(--muted-fg)', marginTop: 4 }}>
            Persistent stateful VMs — the image must ship its own init system (/sbin/init)
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
              <th style={th}>CPUs</th>
              <th style={th}>Memory</th>
              <th style={th}>Disk</th>
              <th style={th}>Default</th>
              <th style={th}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {!machines?.length && !isLoading && (
              <tr><td colSpan={7} style={{ padding: 24, textAlign: 'center', color: 'var(--muted-fg)' }}>No machines. Create one with + New</td></tr>
            )}
            {machines?.map(m => (
              <MachineRow key={m.id} machine={m} onInspect={setInspectId} />
            ))}
          </tbody>
        </table>
      </GlassBox>

      {inspectId && <InspectModal id={inspectId} onClose={() => setInspectId(null)} />}
      {showCreate && (
        <Modal onClose={() => setShowCreate(false)}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>New Machine</h3>
          <CreateMachineForm onDone={() => setShowCreate(false)} />
        </Modal>
      )}
    </Box>
  )
}

function MachineRow({ machine: m, onInspect }: { machine: MachineInfo; onInspect: (id: string) => void }) {
  const setDefault = useSetMachineDefault()
  const stop = useStopMachine()
  const del = useDeleteMachine()
  const run = useRunMachine()
  const [setOpen, setSetOpen] = useState(false)
  const [runOpen, setRunOpen] = useState(false)
  const [cmd, setCmd] = useState('')
  const [runOpts, setRunOpts] = useState<Record<string, string | boolean>>({})
  const [runResult, setRunResult] = useState<MachineRunResult | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [logsOpen, setLogsOpen] = useState(false)
  const running = m.status === 'running'

  const submitRun = (e: React.FormEvent) => {
    e.preventDefault()
    const parts = cmd.trim().split(/\s+/).filter(Boolean)
    if (!parts.length) return
    setRunResult(null)
    setRunError(null)
    const body: MachineRunBody = { command: parts, ...foldRunFields(runOpts) }
    run.mutate({ id: m.id, body }, {
      onSuccess: r => setRunResult(r),
      onError: err => setRunError((err as Error).message),
    })
  }

  return (
    <tr style={{ borderBottom: '1px solid var(--border)', verticalAlign: 'top' }}>
      <td style={td}>
        <div style={{ fontFamily: 'monospace', fontSize: 13 }}>{m.id}</div>
        {m.image_ref && <div style={{ fontSize: 11, color: 'var(--muted-fg)', marginTop: 2 }}>{m.image_ref.replace('docker.io/library/', '')}</div>}
        {m.warning && (
          <div title={m.warning} style={{ fontSize: 11, color: '#f59e0b', marginTop: 2, background: '#f59e0b20', border: '1px solid #f59e0b', borderRadius: 4, padding: '2px 6px', width: 'fit-content' }}>
            ⚠ likely lacks init
          </div>
        )}
      </td>
      <td style={td}>
        <Badge color={running ? '#22c55e' : '#71717a'}>{m.status || 'unknown'}</Badge>
      </td>
      <td style={{ ...td, fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{m.cpus || '—'}</td>
      <td style={{ ...td, fontSize: 12, color: 'var(--muted-fg)', fontVariantNumeric: 'tabular-nums' }}>{formatBytes(m.memory_bytes)}</td>
      <td style={{ ...td, fontSize: 12, color: 'var(--muted-fg)', fontVariantNumeric: 'tabular-nums' }}>{formatBytes(m.disk_size_bytes)}</td>
      <td style={{ ...td, fontSize: 13, textAlign: 'center' }}>{m.default ? '✓' : '—'}</td>
      <td style={td}>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          <button onClick={() => onInspect(m.id)} style={btnStyle}>Inspect</button>
          <button onClick={() => setSetOpen(!setOpen)} style={{ ...btnStyle, color: setOpen ? '#3b82f6' : 'var(--fg)' }}>Set</button>
          {!m.default && <button onClick={() => setDefault.mutate(m.id)} style={btnStyle} disabled={setDefault.isPending}>Set default</button>}
          {running && <button onClick={() => stop.mutate(m.id)} style={btnStyle} disabled={stop.isPending}>Stop</button>}
          <button onClick={() => setRunOpen(!runOpen)} style={{ ...btnStyle, color: runOpen ? '#3b82f6' : 'var(--fg)' }}>Run</button>
          <button onClick={() => setLogsOpen(!logsOpen)} style={{ ...btnStyle, color: logsOpen ? '#3b82f6' : 'var(--fg)' }}>Logs</button>
          <button
            onClick={() => { if (confirm(`Delete machine ${m.id}?`)) del.mutate(m.id) }}
            style={dangerBtn}
            disabled={del.isPending}
          >Delete</button>
        </div>

        {setOpen && <SetForm machine={m} onDone={() => setSetOpen(false)} />}

        {runOpen && (
          <form onSubmit={submitRun} style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <input value={cmd} onChange={e => setCmd(e.target.value)} placeholder="e.g. uname -a" style={{ ...input, flex: 1 }} />
              <button type="submit" disabled={run.isPending} style={{ ...btnStyle, color: '#22c55e' }}>{run.isPending ? 'Running...' : 'Run'}</button>
            </div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {RUN_OPTION_FIELDS.filter(f => f.kind === 'bool').map(f => (
                <label key={f.key} style={{ fontSize: 11, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                  <input type="checkbox" checked={runOpts[f.key] === true} onChange={e => setRunOpts(v => ({ ...v, [f.key]: e.target.checked }))} />
                  {f.label}
                </label>
              ))}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
              {RUN_OPTION_FIELDS.filter(f => f.kind !== 'bool').map(f => (
                <label key={f.key} style={{ ...label, fontSize: 11 }}>
                  {f.label}
                  <input value={String(runOpts[f.key] ?? '')} onChange={e => setRunOpts(v => ({ ...v, [f.key]: e.target.value }))} placeholder={f.placeholder} style={{ ...input, padding: '4px 8px', fontSize: 12 }} />
                </label>
              ))}
            </div>
            {runError && <div style={{ fontSize: 11, color: '#ef4444' }}>{runError}</div>}
            {runResult && (
              <div style={{ fontSize: 11 }}>
                <div>
                  exit code: <span style={{ color: runResult.exit_code === 0 ? '#22c55e' : '#ef4444', fontFamily: 'monospace' }}>{runResult.exit_code}</span>
                </div>
                {runResult.stdout && <pre style={preStyle}>{runResult.stdout}</pre>}
                {runResult.stderr && <pre style={{ ...preStyle, color: '#ef4444' }}>{runResult.stderr}</pre>}
                {runResult.explanation && (
                  <div style={{ background: '#f59e0b20', border: '1px solid #f59e0b', color: '#f59e0b', borderRadius: 6, padding: 6, marginTop: 4 }}>
                    {runResult.explanation}
                  </div>
                )}
              </div>
            )}
          </form>
        )}

        {logsOpen && <LogsPane id={m.id} />}
      </td>
    </tr>
  )
}

function SetForm({ machine: m, onDone }: { machine: MachineInfo; onDone: () => void }) {
  const [cpus, setCpus] = useState('')
  const [memory, setMemory] = useState('')
  const [homeMount, setHomeMount] = useState('') // '' = leave unchanged
  const [virt, setVirt] = useState('') // '' | 'true' | 'false' — '' = leave unchanged
  const setM = useSetMachine()

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const body: MachineSetBody = {}
    const c = parseInt(cpus)
    if (cpus.trim() !== '' && !isNaN(c)) body.cpus = c
    if (memory.trim()) body.memory = memory.trim()
    if (homeMount) body.home_mount = homeMount
    if (virt === 'true') body.virtualization = true
    if (virt === 'false') body.virtualization = false
    setM.mutate({ id: m.id, body }, { onSuccess: onDone })
  }

  return (
    <form onSubmit={submit} style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <label style={label}>CPUs <input type="number" value={cpus} onChange={e => setCpus(e.target.value)} placeholder={`current: ${m.cpus}`} style={input} /></label>
        <label style={label}>Memory <input value={memory} onChange={e => setMemory(e.target.value)} placeholder="e.g. 2G" style={input} /></label>
        <label style={label}>
          Home mount
          <select value={homeMount} onChange={e => setHomeMount(e.target.value)} style={selectStyle}>
            <option value="">(unchanged — current: {m.home_mount || '?'})</option>
            <option value="rw">rw</option>
            <option value="ro">ro</option>
            <option value="none">none</option>
          </select>
        </label>
        <label style={label}>
          Virtualization
          <select value={virt} onChange={e => setVirt(e.target.value)} style={selectStyle}>
            <option value="">(unchanged)</option>
            <option value="true">enabled</option>
            <option value="false">disabled</option>
          </select>
        </label>
      </div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <button type="submit" disabled={setM.isPending} style={{ ...btnStyle, color: '#3b82f6' }}>{setM.isPending ? 'Saving...' : 'Apply'}</button>
        <button type="button" onClick={onDone} style={btnStyle}>Cancel</button>
        <span style={{ fontSize: 11, color: 'var(--muted-fg)' }}>changes apply after restart</span>
      </div>
      {setM.isError && <div style={{ fontSize: 11, color: '#ef4444' }}>{(setM.error as Error).message}</div>}
    </form>
  )
}

function LogsPane({ id }: { id: string }) {
  const [boot, setBoot] = useState(false)
  const [tail, setTail] = useState('')
  const { data, isLoading, error } = useMachineLogs(id, {
    boot,
    tail: tail.trim() ? (parseInt(tail) || undefined) : undefined,
  })
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 6 }}>
        <label style={{ fontSize: 11, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 4 }}>
          <input type="checkbox" checked={boot} onChange={e => setBoot(e.target.checked)} />
          Boot log
        </label>
        <input value={tail} onChange={e => setTail(e.target.value)} placeholder="tail lines" style={{ ...input, width: 90, padding: '4px 8px', fontSize: 12 }} />
      </div>
      {isLoading && <div style={{ fontSize: 11, color: 'var(--muted-fg)' }}>Loading logs...</div>}
      {error && <div style={{ fontSize: 11, color: '#ef4444' }}>{(error as Error).message}</div>}
      {data && <pre style={{ ...preStyle, maxHeight: 300 }}>{data.logs || '(no logs)'}</pre>}
    </div>
  )
}

function InspectModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { data } = useInspectMachine(id)
  return (
    <Modal onClose={onClose}>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Inspect {id}</h3>
      <pre style={{ fontSize: 12, maxHeight: 500, overflow: 'auto', background: '#0f0f13', padding: 12, borderRadius: 8, whiteSpace: 'pre-wrap' }}>
        {data ? JSON.stringify(data, null, 2) : 'Loading...'}
      </pre>
    </Modal>
  )
}

function CreateMachineForm({ onDone }: { onDone: () => void }) {
  const [image, setImage] = useState('')
  const [name, setName] = useState('')
  const [cpus, setCpus] = useState('')
  const [memory, setMemory] = useState('')
  const [homeMount, setHomeMount] = useState('rw')
  const [virtualization, setVirtualization] = useState(false)
  const [noBoot, setNoBoot] = useState(true) // create-without-booting is the safe default
  const [setDefault, setSetDefault] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const create = useCreateMachine()

  return (
    <form onSubmit={e => {
      e.preventDefault()
      const body: MachineCreateBody = {
        image: image.trim(),
        virtualization,
        set_default: setDefault,
        no_boot: noBoot,
      }
      if (name.trim()) body.name = name.trim()
      const c = parseInt(cpus)
      if (cpus.trim() !== '' && !isNaN(c)) body.cpus = c
      if (memory.trim()) body.memory = memory.trim()
      if (homeMount) body.home_mount = homeMount
      setWarning(null)
      create.mutate(body, {
        onSuccess: info => {
          // Surface the init-capability advisor: keep the modal open with an
          // amber banner so the warning is actually seen.
          if (info.warning) setWarning(info.warning)
          else onDone()
        },
      })
    }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <label style={label}>Image <input value={image} onChange={e => setImage(e.target.value)} placeholder="e.g. ubuntu:24.04" style={input} required /></label>
      <label style={label}>Name <input value={name} onChange={e => setName(e.target.value)} placeholder="optional" style={input} /></label>
      <div style={{ display: 'flex', gap: 12 }}>
        <label style={{ ...label, flex: 1 }}>CPUs <input type="number" value={cpus} onChange={e => setCpus(e.target.value)} placeholder="optional" style={input} /></label>
        <label style={{ ...label, flex: 1 }}>Memory <input value={memory} onChange={e => setMemory(e.target.value)} placeholder="e.g. 2G" style={input} /></label>
      </div>
      <label style={label}>
        Home mount
        <select value={homeMount} onChange={e => setHomeMount(e.target.value)} style={selectStyle}>
          <option value="rw">rw — share home read-write</option>
          <option value="ro">ro — share home read-only</option>
          <option value="none">none — no home sharing</option>
        </select>
      </label>
      <label style={{ ...label, flexDirection: 'row', alignItems: 'center', gap: 6, width: 'fit-content' }}>
        <input type="checkbox" checked={virtualization} onChange={e => setVirtualization(e.target.checked)} />
        Virtualization (KVM kernel, M3+)
      </label>
      <label style={{ ...label, flexDirection: 'row', alignItems: 'center', gap: 6, width: 'fit-content' }}>
        <input type="checkbox" checked={noBoot} onChange={e => setNoBoot(e.target.checked)} />
        Create without booting
      </label>
      <label style={{ ...label, flexDirection: 'row', alignItems: 'center', gap: 6, width: 'fit-content' }}>
        <input type="checkbox" checked={setDefault} onChange={e => setSetDefault(e.target.checked)} />
        Set as default machine
      </label>

      {warning && (
        <div style={{ background: '#f59e0b20', border: '1px solid #f59e0b', color: '#f59e0b', borderRadius: 6, padding: '8px 12px', fontSize: 12 }}>
          ⚠ {warning}
        </div>
      )}

      <button type="submit" disabled={!image.trim() || create.isPending} style={primaryBtn}>
        {create.isPending ? 'Creating...' : 'Create machine'}
      </button>
      {create.isError && <p style={{ color: '#ef4444', fontSize: 12 }}>{(create.error as Error).message}</p>}
    </form>
  )
}
