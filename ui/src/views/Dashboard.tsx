import { useSystemStatus } from '../api/system'
import { useContainers } from '../api/containers'
import { useImages } from '../api/images'
import { Box, GlassBox, StatCard, Badge } from './shared'

export default function Dashboard() {
  const { data: status } = useSystemStatus()
  const { data: containers, isLoading: loadingContainers } = useContainers(true)
  const { data: images, isLoading: loadingImages } = useImages()

  const running = containers?.filter(c => c.state === 'running').length ?? 0
  const stopped = (containers?.length ?? 0) - running

  const disk = status?.disk_usage
  const diskError = disk && 'error' in disk ? String(disk.error) : null
  const diskUsed = typeof disk?.used === 'number' ? disk.used : 0
  const diskTotal = typeof disk?.total === 'number' && disk.total > 0 ? disk.total : 0
  const diskPct = diskTotal > 0 ? ((diskUsed / diskTotal) * 100).toFixed(1) : '0'

  return (
    <Box>
      <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 24 }}>System Overview</h2>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16, marginBottom: 32 }}>
        <StatCard label="Daemon" value={status?.apiserver_running ? 'Running' : 'Stopped'}
          color={status?.apiserver_running ? 'var(--success)' : 'var(--danger)'} />
        <StatCard label="Container v" value={status?.container_version ?? '...'} />
        <StatCard label="Total Containers" value={loadingContainers ? '...' : String(containers?.length ?? 0)} />
        <StatCard label="Running" value={loadingContainers ? '...' : String(running)} color="var(--success)" />
        <StatCard label="Stopped" value={loadingContainers ? '...' : String(stopped)} color="var(--muted-fg)" />
        <StatCard label="Images" value={loadingImages ? '...' : String(images?.length ?? 0)} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
        <GlassBox>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--muted-fg)' }}>Disk Usage</h3>
          {diskError ? (
            <p style={{ fontSize: 12, color: 'var(--muted-fg)' }}>Unavailable — {diskError}</p>
          ) : (
            <>
              <div style={{ height: 8, background: '#27272a', borderRadius: 4, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${diskPct}%`, background: 'linear-gradient(90deg, #3b82f6, #2563eb)', borderRadius: 4, transition: 'width 0.5s' }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 12, color: 'var(--muted-fg)' }}>
                <span>{formatBytes(diskUsed)} used</span>
                <span>{diskTotal > 0 ? formatBytes(diskTotal) + ' total' : '—'}</span>
              </div>
            </>
          )}
        </GlassBox>

        <GlassBox>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--muted-fg)' }}>Container States</h3>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Badge color="var(--success)">Running: {running}</Badge>
            <Badge>Stopped: {stopped}</Badge>
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted-fg)', marginTop: 12 }}>
            Commit: {status?.container_commit ?? '...'}
          </div>
        </GlassBox>

        <GlassBox>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--muted-fg)' }}>Runtime Features</h3>
          <div style={{ fontSize: 12, color: 'var(--muted-fg)' }}>
            {(() => {
              const rf = (status?.capabilities as { runtime_features?: Record<string, boolean> } | undefined)?.runtime_features
              if (!rf) return '...'
              return Object.entries(rf).map(([k, v]) => (
                <div key={k} style={{ marginBottom: 4 }}>
                  {v ? '✓' : '✗'} {k.replace(/_/g, ' ')}
                </div>
              ))
            })()}
          </div>
        </GlassBox>

        <GlassBox>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--muted-fg)' }}>Capabilities</h3>
          <div style={{ fontSize: 12, color: 'var(--muted-fg)' }}>
            {status?.capabilities ? Object.entries(status.capabilities).filter(([,v]) => v === true).slice(0, 6).map(([k]) => (
              <div key={k} style={{ marginBottom: 4 }}>✓ {k.replace(/_/g, ' ')}</div>
            )) : '...'}
          </div>
        </GlassBox>
      </div>
    </Box>
  )
}

function formatBytes(b: number): string {
  if (b > 1e12) return (b / 1e12).toFixed(1) + ' TB'
  if (b > 1e9) return (b / 1e9).toFixed(1) + ' GB'
  if (b > 1e6) return (b / 1e6).toFixed(1) + ' MB'
  return b.toLocaleString() + ' B'
}
