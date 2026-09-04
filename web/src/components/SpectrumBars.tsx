const PLACEHOLDER_COUNT = 30

interface Props {
  bars: number[]
}

export function SpectrumBars({ bars }: Props) {
  const data = bars.length > 0 ? bars : Array(PLACEHOLDER_COUNT).fill(0)

  return (
    <div className="h-20 flex items-end gap-px">
      {data.map((v, i) => (
        <div
          key={i}
          className="flex-1 rounded-sm"
          style={{
            height: `${Math.max(v * 100, 2)}%`,
            // Accent colour, opacity tracks amplitude so silent bars remain faintly visible.
            backgroundColor: 'hsl(var(--accent))',
            opacity: Math.max(v, 0.18),
            transition: 'height 33ms linear, opacity 33ms linear',
          }}
        />
      ))}
    </div>
  )
}
