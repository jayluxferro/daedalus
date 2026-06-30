import { Fragment, useState } from 'react'
import { useExperiments } from '../api/experiments'
import { Box, GlassBox } from './shared'

export default function ExperimentsView() {
  const { data: experiments, isLoading } = useExperiments()
  const [expanded, setExpanded] = useState<string | null>(null)

  return (
    <Box>
      <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 20 }}>Experiments</h2>
      <p style={{ fontSize: 13, color: 'var(--muted-fg)', marginBottom: 16 }}>
        Run manifests and artifacts from the DAEDALUS store.
      </p>

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
                  </tr>
                  {expanded === e.run_id && (
                    <tr>
                      <td colSpan={6} style={{ padding: 12, background: '#0f0f13' }}>
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
