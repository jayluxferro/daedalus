export function Box({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div style={{ maxWidth: 1200, ...style }}>{children}</div>
}

export function GlassBox({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 8,
      padding: 16, ...style,
    }}>
      {children}
    </div>
  )
}

export function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <GlassBox style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 24, fontWeight: 700, color: color || 'var(--fg)', fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
      <div style={{ fontSize: 11, color: 'var(--muted-fg)', marginTop: 4 }}>{label}</div>
    </GlassBox>
  )
}

export function Badge({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 20,
      background: color ? `${color}20` : '#27272a',
      color: color || 'var(--muted-fg)', fontSize: 11, fontWeight: 500,
      border: `1px solid ${color || 'var(--border)'}`,
    }}>
      {children}
    </span>
  )
}

export function Modal({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 100,
    }} onClick={onClose}>
      <div style={{
        background: '#0f0f13', border: '1px solid var(--border)', borderRadius: 12,
        padding: 24, minWidth: 400, maxWidth: 600, maxHeight: '80vh', overflow: 'auto',
      }} onClick={e => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}
