/**
 * Minimal inline-SVG sparkline — no chart library, no state.
 *
 * Values are mapped to points with x spread evenly across the width and
 * y inverted (higher value → higher on screen) with 2 px padding; the
 * polyline gets a low-opacity area fill.  With fewer than 2 values a
 * muted dashed flat line is rendered instead (nothing meaningful to
 * plot yet).  Colors ride on CSS vars / callers' palette tokens so the
 * sparkline matches the tile it lives under.
 */
export default function Sparkline({
  values,
  width = 64,
  height = 18,
  color = '#22c55e',
}: {
  values: number[]
  width?: number
  height?: number
  color?: string
}) {
  const pad = 2
  const n = values.length

  let points = ''
  let area = ''
  if (n >= 2) {
    const min = Math.min(...values)
    const max = Math.max(...values)
    const span = Math.max(max - min, 1e-9) // flat series → mid-height line
    const x = (i: number) => pad + (i / (n - 1)) * (width - 2 * pad)
    const y = (v: number) => height - pad - ((v - min) / span) * (height - 2 * pad)
    points = values.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ')
    area = `${pad},${height - pad} ${points} ${width - pad},${height - pad}`
  }

  const extent = n >= 2
    ? `min ${Math.min(...values).toFixed(1)}, max ${Math.max(...values).toFixed(1)}`
    : 'no data'

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Sparkline: ${extent}`}
      style={{ display: 'block' }}
    >
      {n >= 2 ? (
        <>
          <polygon points={area} fill={color} opacity={0.15} />
          <polyline
            points={points}
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </>
      ) : (
        <line
          x1={pad}
          y1={height / 2}
          x2={width - pad}
          y2={height / 2}
          stroke="var(--muted-fg)"
          strokeWidth={1}
          strokeDasharray="2 2"
        />
      )}
    </svg>
  )
}
