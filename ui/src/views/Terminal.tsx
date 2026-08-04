import { useEffect, useRef, useState, useCallback } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { Box, GlassBox } from './shared'

interface TerminalViewProps {
  containerId: string
  onClose?: () => void
}

const PROMPT = '\x1b[36mλ \x1b[0m'

export default function TerminalView({ containerId, onClose }: TerminalViewProps) {
  const termRef = useRef<HTMLDivElement>(null)
  const term = useRef<XTerm | null>(null)
  const fitAddon = useRef<FitAddon | null>(null)
  const [sending, setSending] = useState(false)
  const lineBuf = useRef('')
  const history = useRef<string[]>([])
  const historyIdx = useRef(0)

  const send = useCallback(async (cmd: string) => {
    if (cmd === 'exit' || cmd === 'logout') {
      term.current?.writeln('\r\n\x1b[33mClosing terminal...\x1b[0m')
      setTimeout(() => onClose?.(), 300)
      return
    }

    setSending(true)
    // Auto-wrap interactive commands to batch/non-interactive mode
    let execCmd: string
    if (cmd === 'top' || cmd === 'htop') {
      execCmd = 'top -bn1 2>/dev/null || top -n 1 -b 2>/dev/null || echo "top not available"'
    } else {
      execCmd = cmd
    }
    try {
      const res = await fetch(`/containers/${containerId}/exec`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: ['sh', '-c', execCmd], timeout: 10 }),
      })
      if (!res.ok) {
        term.current?.writeln(`\r\n\x1b[31mError: ${res.status}\x1b[0m`)
        return
      }
      const data = await res.json()
      if (data.stdout) {
        for (const line of data.stdout.split('\n')) {
          term.current?.writeln('\r' + line)
        }
      }
      if (data.stderr) {
        for (const line of data.stderr.split('\n')) {
          if (line.trim()) term.current?.writeln('\r\x1b[31m' + line + '\x1b[0m')
        }
      }
      if (data.exit_code !== 0 && data.exit_code !== undefined) {
        term.current?.writeln(`\r\x1b[33m[exit ${data.exit_code}]\x1b[0m`)
      }
    } catch (err) {
      term.current?.writeln(`\r\n\x1b[31m${err}\x1b[0m`)
    } finally {
      setSending(false)
      term.current?.write('\r\n' + PROMPT)
      lineBuf.current = ''
    }
  }, [containerId, onClose])

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
    fitAddon.current = fit

    try { const wg = new WebglAddon(); t.loadAddon(wg); wg.onContextLoss(() => wg.dispose()) } catch { /* fallback */ }

    if (termRef.current) { t.open(termRef.current); fit.fit() }

    t.writeln('\x1b[36mConnected to \x1b[33m' + containerId.slice(0, 12) + '\x1b[0m')
    t.writeln('Type commands, Enter to run. \x1b[33mexit\x1b[0m to close. ↑ for history.')
    t.write('\r\n' + PROMPT)

    term.current = t

    t.onData((data) => {
      if (sending) return

      if (data === '\r') {
        t.write('\r\n')
        const cmd = lineBuf.current.trim()
        if (cmd) {
          history.current.push(cmd)
          historyIdx.current = history.current.length
          send(cmd)
        } else {
          lineBuf.current = ''
          t.write(PROMPT)
        }
        return
      }

      if (data === '\x03' || data === '\x04') {
        t.write('^C\r\n' + PROMPT)
        lineBuf.current = ''
        historyIdx.current = history.current.length
        return
      }

      if (data === '\x7f') {
        if (lineBuf.current.length > 0) {
          lineBuf.current = lineBuf.current.slice(0, -1)
          t.write('\b \b')
        }
        return
      }

      if (data === '\x1b[A') {
        if (history.current.length > 0 && historyIdx.current > 0) {
          historyIdx.current--
          t.write('\r\x1b[K' + PROMPT + history.current[historyIdx.current])
          lineBuf.current = history.current[historyIdx.current]
        }
        return
      }

      if (data === '\x1b[B') {
        t.write('\r\x1b[K')
        if (historyIdx.current < history.current.length - 1) {
          historyIdx.current++
          lineBuf.current = history.current[historyIdx.current]
          t.write(PROMPT + history.current[historyIdx.current])
        } else {
          historyIdx.current = history.current.length
          lineBuf.current = ''
          t.write(PROMPT)
        }
        return
      }

      if (data.length === 1 && data.charCodeAt(0) >= 0x20) {
        lineBuf.current += data
        t.write(data)
      }
    })

    const handleResize = () => fit.fit()
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      t.dispose()
    }
  }, [containerId, send])

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
