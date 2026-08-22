import { Fragment, useState } from 'react'
import { useExperiments, useDeleteExperiment, useClearExperiments, useReport } from '../api/experiments'
import { Box, GlassBox, Badge, StatCard } from './shared'

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
                        <ReportPanel runId={e.run_id} />
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

function ReportPanel({ runId }: { runId: string }) {
  const { data: report, isLoading, isError } = useReport(runId)

  if (isLoading) return <p style={{ fontSize: 12, color: 'var(--muted-fg)' }}>Loading report...</p>
  if (isError) return <p style={{ fontSize: 12, color: 'var(--muted-fg)' }}>Failed to load behavioral report.</p>
  if (!report) return <p style={{ fontSize: 12, color: 'var(--muted-fg)' }}>No behavioral report for this run.</p>

  const risk = riskLabel(report.risk_score ?? 0)
  const fs = report.filesystem ?? { added: [], modified: [], removed: [], added_count: 0, modified_count: 0, removed_count: 0, total_changes: 0 }
  const net = report.network ?? { total_bytes_sent: 0, total_bytes_received: 0 }
  const res = report.resources ?? {}
  const netBytes = (net.total_bytes_sent ?? 0) + (net.total_bytes_received ?? 0)

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0 }}>Behavioral Report</h3>
          <div style={{ fontSize: 11, color: 'var(--muted-fg)', marginTop: 2 }}>
            <code>{report.run_id}</code>
            {report.generated_at ? ` · ${new Date(report.generated_at).toLocaleString()}` : ''}
          </div>
        </div>
        <Badge color={risk.color}>{risk.label} · {(report.risk_score ?? 0).toFixed(2)}</Badge>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 16 }}>
        <StatCard label="FS changes (added/modified/removed)"
          value={`+${fs.added_count ?? 0} ~${fs.modified_count ?? 0} −${fs.removed_count ?? 0}`}
          color={fs.total_changes > 0 ? 'var(--warning)' : undefined} />
        <StatCard label="Network bytes (sent + received)" value={formatBytes(netBytes)} />
        <StatCard label="Peak CPU" value={(res.peak_cpu_percent ?? 0).toFixed(1) + '%'} />
        <StatCard label="Peak memory" value={(res.peak_memory_mb ?? 0).toFixed(1) + ' MB'} />
        <StatCard label="Duration" value={(res.duration_seconds ?? 0).toFixed(1) + 's'} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
        <GlassBox>
          <h4 style={{ fontSize: 12, fontWeight: 600, marginBottom: 10, color: 'var(--muted-fg)', textTransform: 'uppercase' }}>Risk Factors</h4>
          {report.risk_factors?.length ? report.risk_factors.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: 'var(--fg)', marginBottom: 6, paddingLeft: 10, borderLeft: '2px solid var(--warning)' }}>{f}</div>
          )) : <p style={{ fontSize: 12, color: 'var(--muted-fg)', margin: 0 }}>None</p>}

          <h4 style={{ fontSize: 12, fontWeight: 600, margin: '18px 0 10px', color: 'var(--muted-fg)', textTransform: 'uppercase' }}>Findings</h4>
          {report.findings?.length ? report.findings.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: 'var(--muted-fg)', marginBottom: 6, display: 'flex', gap: 8 }}>
              <span style={{ color: 'var(--accent)' }}>•</span>
              <span>{f}</span>
            </div>
          )) : <p style={{ fontSize: 12, color: 'var(--muted-fg)', margin: 0 }}>None</p>}
        </GlassBox>

        <GlassBox>
          <h4 style={{ fontSize: 12, fontWeight: 600, marginBottom: 10, color: 'var(--muted-fg)', textTransform: 'uppercase' }}>Filesystem Changes</h4>
          <FsSection title="Added" paths={fs.added ?? []} />
          <FsSection title="Modified" paths={fs.modified ?? []} />
          <FsSection title="Removed" paths={fs.removed ?? []} />
        </GlassBox>
      </div>
    </div>
  )
}

function FsSection({ title, paths }: { title: string; paths: string[] }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 11, color: 'var(--muted-fg)', marginBottom: 4 }}>
        {title} ({paths.length})
      </div>
      {paths.length ? (
        <pre style={{ fontSize: 11, margin: 0, maxHeight: 96, overflow: 'auto', padding: 8, background: '#09090b', border: '1px solid var(--border)', borderRadius: 6 }}>
          {paths.slice(0, 50).join('\n')}
        </pre>
      ) : (
        <p style={{ fontSize: 11, color: 'var(--muted-fg)', margin: 0 }}>None</p>
      )}
    </div>
  )
}

function riskLabel(score: number): { label: string; color: string } {
  if (score >= 0.7) return { label: 'HIGH', color: 'var(--danger)' }
  if (score >= 0.3) return { label: 'MEDIUM', color: 'var(--warning)' }
  if (score > 0) return { label: 'LOW', color: 'var(--success)' }
  return { label: 'NONE', color: 'var(--muted-fg)' }
}

function formatBytes(b: number): string {
  if (b > 1e12) return (b / 1e12).toFixed(1) + ' TB'
  if (b > 1e9) return (b / 1e9).toFixed(1) + ' GB'
  if (b > 1e6) return (b / 1e6).toFixed(1) + ' MB'
  return b.toLocaleString() + ' B'
}
