import { Fragment, useState } from 'react'
import { useExperiments, useDeleteExperiment, useClearExperiments } from '../api/experiments'
import { Box, GlassBox } from './shared'

export default function ExperimentsView() {
  const { data: experiments, isLoading } = useExperiments()
  const [expanded, setExpanded] = useState<string | null>(null)
  const deleteExperiment = useDeleteExperiment()
  const clearExperiments = useClearExperiments()

  return (
    <Box>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Experiments</h2>
        {experiments && experiments.length > 0 && (
          <button onClick={() => { if (confirm('Delete all experiment records?')) clearExperiments.mutate() }}
            style={{ background: '#ef444420', border: '1px solid #ef444440', color: '#ef4444', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontSize: 12 }}>
            Clear All ({experiments.length})
          </button>
        )}
      </div>
      <p style={{ fontSize: 13, color: 'var(--muted-fg)', marginBottom: 16 }}>
        Run manifests record every container created — image, profile, command, artifacts.
      </p>

      {clearExperiments.isPending && <p style={{ color: 'var(--muted-fg)' }}>Clearing...</p>}

      {isLoading ? (
        <p style={{ color: 'var(--muted-fg)' }}>Loading...</p>
      ) : !experiments?.length ? (
        <p style={{ color: 'var(--muted-fg)' }}>No experiments recorded yet. Create a container to start.</p>
      ) : (
        <GlassBox style={{ padding: 0, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--muted-fg)', textAlign: 'left' }}>
                <th style={th}>Run ID</th>
                <th style={th}>Image</th>
                <th style={th}>Profile</th>
                <th style={th}>Created</th>
                <th style={th}>Exit</th>
                <th style={th}>Artifacts</th>
                <th style={th}></th>
              </tr>
            </thead>
            <tbody>
              {experiments.map(e => (
                <Fragment key={e.run_id}>
                  <tr style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
                    onClick={() => setExpanded(expanded === e.run_id ? null : e.run_id)}>
                    <td style={td}><code>{e.run_id.slice(0, 12)}</code></td>
                    <td style={td}>{e.image.replace('docker.io/library/', '')}</td>
                    <td style={td}>{e.profile}</td>
                    <td style={td}>{e.created_at ? new Date(e.created_at).toLocaleString() : '—'}</td>
                    <td style={td}>{e.exit_code ?? '—'}</td>
                    <td style={td}>{e.artifacts?.length ?? 0}</td>
                    <td style={td}>
                      <button onClick={ev => { ev.stopPropagation(); deleteExperiment.mutate(e.run_id) }}
                        style={{ background: 'transparent', border: '1px solid var(--border)', color: '#ef4444', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', fontSize: 11 }}>
                        ✕
                      </button>
                    </td>
                  </tr>
                  {expanded === e.run_id && (
                    <tr>
                      <td colSpan={7} style={{ padding: 12, background: '#0f0f13' }}>
                        <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>
                          {JSON.stringify(e, null, 2)}
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

const th: React.CSSProperties = { padding: '8px 12px', fontSize: 10, textTransform: 'uppercase' }
const td: React.CSSProperties = { padding: '6px 12px' }
