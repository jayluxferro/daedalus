import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { API_BASE } from '../api/client'
import { Box, GlassBox } from './shared'

interface TerminalViewProps {
  containerId: string
  onClose?: () => void
}

export default function TerminalView({ containerId, onClose }: TerminalViewProps) {
  const termRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const t = new XTerm({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: {
        background: '#0a0a0f', foreground: '#d4d4d8', cursor: '#3b82f6',
        selectionBackground: '#3b82f640', black: '#18181b', red: '#ef4444',
        green: '#22c55e', yellow: '#eab308', blue: '#3b82f6',
        magenta: '#a855f7', cyan: '#06b6d4', white: '#d4d4d8',
      },
    })

    const fit = new FitAddon()
    t.loadAddon(fit)

    try {
      const wg = new WebglAddon()
      t.loadAddon(wg)
      wg.onContextLoss(() => wg.dispose())
    } catch { /* fallback to canvas */ }

    if (termRef.current) {
      t.open(termRef.current)
      fit.fit()
    }

    const wsUrl = API_BASE.replace(/^http/, 'ws') + `/containers/${containerId}/exec`
    const ws = new WebSocket(wsUrl)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => fit.fit()

    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        t.write(new Uint8Array(ev.data))
      } else {
        t.write(String(ev.data))
      }
    }

    ws.onclose = () => {
      t.writeln('\r\n\x1b[33m[disconnected]\x1b[0m')
    }

    ws.onerror = () => {
      t.writeln('\r\n\x1b[31m[connection error]\x1b[0m')
    }

    t.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data)
      }
    })

    const handleResize = () => fit.fit()
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      ws.close()
      t.dispose()
    }
  }, [containerId])

  return (
    <Box>
      <GlassBox style={{ marginBottom: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>☿ Terminal</span>
          <span style={{ fontSize: 12, color: 'var(--muted-fg)', fontFamily: 'monospace', letterSpacing: 1 }}>
            {containerId.slice(0, 12)}
          </span>
          <span style={{ fontSize: 11, color: '#22c55e' }}>● connected</span>
        </div>
        {onClose && (
          <button onClick={onClose} style={{
            background: 'transparent', border: '1px solid var(--border)',
            color: 'var(--fg)', borderRadius: 6, padding: '4px 12px',
            cursor: 'pointer', fontSize: 12,
          }}>
            Close
          </button>
        )}
      </GlassBox>
      <div ref={termRef} style={{
        width: '100%', height: 'calc(100vh - 200px)', minHeight: 400,
        borderRadius: 8, overflow: 'hidden',
      }} />
    </Box>
  )
}
