import { useState } from 'react'
import { useImages, usePullImage, useDeleteImage, useInspectImage, useBuildImage, usePushImage, useLoadImage, imageRef } from '../api/images'
import { Box, GlassBox, Modal } from './shared'

export default function ImagesView() {
  const { data: images, isLoading } = useImages()
  const [showPull, setShowPull] = useState(false)
  const [showBuild, setShowBuild] = useState(false)
  const [showLoad, setShowLoad] = useState(false)
  const [inspectName, setInspectName] = useState<string | null>(null)
  const deleteMutation = useDeleteImage()
  const pushMutation = usePushImage()

  return (
    <Box>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Images</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowLoad(true)} style={secondaryBtn}>Load tar</button>
          <button onClick={() => setShowBuild(true)} style={secondaryBtn}>Build</button>
          <button onClick={() => setShowPull(true)} style={btnStyle}>+ Pull Image</button>
        </div>
      </div>

      {isLoading ? (
        <p style={{ color: 'var(--muted-fg)' }}>Loading...</p>
      ) : !images?.length ? (
        <p style={{ color: 'var(--muted-fg)' }}>No images. Pull one to get started.</p>
      ) : (
        <GlassBox style={{ padding: 0, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--muted-fg)', textAlign: 'left' }}>
                <th style={th}>Name</th><th style={th}>Tag</th><th style={th}>Size</th><th style={th}>Digest</th><th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {images.map(img => (
                <tr key={img.id || img.name} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={td}>{img.name}</td>
                  <td style={td}>{img.tag}</td>
                  <td style={td}>{formatBytes(img.size)}</td>
                  <td style={td}><code style={{ fontSize: 11 }}>{img.digest ? img.digest.slice(0, 16) : '—'}</code></td>
                  <td style={{ ...td, display: 'flex', gap: 4 }}>
                    <button onClick={() => setInspectName(imageRef(img))} style={inspectBtn}>Inspect</button>
                    <button onClick={() => pushMutation.mutate(imageRef(img))} disabled={pushMutation.isPending} style={inspectBtn}>
                      Push
                    </button>
                    <button onClick={() => { if (confirm(`Delete ${img.name}?`)) deleteMutation.mutate(imageRef(img)) }} style={actionBtn}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </GlassBox>
      )}

      {showPull && (
        <Modal onClose={() => setShowPull(false)}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Pull Image</h3>
          <PullForm onDone={() => setShowPull(false)} />
        </Modal>
      )}
      {showBuild && (
        <Modal onClose={() => setShowBuild(false)}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Build Image</h3>
          <BuildForm onDone={() => setShowBuild(false)} />
        </Modal>
      )}
      {showLoad && (
        <Modal onClose={() => setShowLoad(false)}>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Load OCI Tar</h3>
          <LoadForm onDone={() => setShowLoad(false)} />
        </Modal>
      )}
      {inspectName && <InspectModal name={inspectName} onClose={() => setInspectName(null)} />}
    </Box>
  )
}

function InspectModal({ name, onClose }: { name: string; onClose: () => void }) {
  const { data, isLoading } = useInspectImage(name)
  return (
    <Modal onClose={onClose}>
      <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Inspect {name}</h3>
      <pre style={{ fontSize: 12, maxHeight: 500, overflow: 'auto', background: '#0f0f13', padding: 12, borderRadius: 8, whiteSpace: 'pre-wrap' }}>
        {isLoading ? 'Loading...' : data ? JSON.stringify(data, null, 2) : 'Not found'}
      </pre>
    </Modal>
  )
}

function PullForm({ onDone }: { onDone: () => void }) {
  const [image, setImage] = useState('')
  const pull = usePullImage()

  return (
    <form onSubmit={e => { e.preventDefault(); pull.mutate(image, { onSuccess: onDone }) }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <label style={label}>Image reference <input value={image} onChange={e => setImage(e.target.value)} placeholder="e.g. alpine:latest" style={input} /></label>
      <button type="submit" disabled={pull.isPending || !image} style={{ ...btnStyle, width: '100%' }}>
        {pull.isPending ? 'Pulling...' : 'Pull'}
      </button>
      {pull.isError && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{(pull.error as Error).message}</p>}
    </form>
  )
}

function BuildForm({ onDone }: { onDone: () => void }) {
  const [tag, setTag] = useState('')
  const [context, setContext] = useState('.')
  const [file, setFile] = useState('')
  const build = useBuildImage()

  return (
    <form onSubmit={e => {
      e.preventDefault()
      build.mutate({
        tag,
        context: context || '.',
        file: file || undefined,
      }, { onSuccess: onDone })
    }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <label style={label}>Tag <input value={tag} onChange={e => setTag(e.target.value)} placeholder="e.g. mylab:latest" style={input} required /></label>
      <label style={label}>Context path <input value={context} onChange={e => setContext(e.target.value)} placeholder="." style={input} /></label>
      <label style={label}>Containerfile <input value={file} onChange={e => setFile(e.target.value)} placeholder="Containerfile (optional)" style={input} /></label>
      <button type="submit" disabled={build.isPending || !tag} style={{ ...btnStyle, width: '100%' }}>
        {build.isPending ? 'Building...' : 'Build'}
      </button>
      {build.isError && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{(build.error as Error).message}</p>}
    </form>
  )
}

function LoadForm({ onDone }: { onDone: () => void }) {
  const [path, setPath] = useState('')
  const load = useLoadImage()

  return (
    <form onSubmit={e => { e.preventDefault(); load.mutate(path, { onSuccess: onDone }) }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <label style={label}>OCI tar path <input value={path} onChange={e => setPath(e.target.value)} placeholder="/path/to/image.tar" style={input} required /></label>
      <button type="submit" disabled={load.isPending || !path} style={{ ...btnStyle, width: '100%' }}>
        {load.isPending ? 'Loading...' : 'Load'}
      </button>
      {load.isError && <p style={{ color: 'var(--danger)', fontSize: 12 }}>{(load.error as Error).message}</p>}
    </form>
  )
}

const btnStyle: React.CSSProperties = { padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 500 }
const secondaryBtn: React.CSSProperties = { ...btnStyle, background: '#1c1c22', border: '1px solid var(--border)', color: 'var(--fg)' }
const th: React.CSSProperties = { padding: '8px 12px', fontSize: 11, textTransform: 'uppercase' }
const td: React.CSSProperties = { padding: '8px 12px' }
const inspectBtn: React.CSSProperties = { padding: '2px 8px', fontSize: 11, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--fg)', cursor: 'pointer' }
const actionBtn: React.CSSProperties = { padding: '2px 8px', fontSize: 11, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--danger)', cursor: 'pointer' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--muted-fg)', display: 'flex', flexDirection: 'column', gap: 4 }
const input: React.CSSProperties = { padding: '8px 12px', background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--fg)', fontSize: 13 }

function formatBytes(b: number): string {
  if (b > 1e9) return (b / 1e9).toFixed(1) + ' GB'
  if (b > 1e6) return (b / 1e6).toFixed(1) + ' MB'
  return b.toLocaleString() + ' B'
}
