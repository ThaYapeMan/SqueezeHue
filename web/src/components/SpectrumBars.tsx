const PLACEHOLDER_COUNT = 30
const BASS_END = 0.15
const MID_END = 0.50

const BAND_COLORS = {
  bass:   'rgb(239, 68, 68)',    // red-500
  mid:    'rgb(34, 197, 94)',    // green-500
  treble: 'rgb(96, 165, 250)',   // blue-400
}

function barBand(i: number, n: number): keyof typeof BAND_COLORS {
  if (i < Math.floor(BASS_END * n)) return 'bass'
  if (i < Math.floor(MID_END * n)) return 'mid'
  return 'treble'
}

function sliceAvg(bars: number[], lo: number, hi: number): number {
  const slice = bars.slice(lo, Math.max(hi, lo + 1))
  return slice.length ? slice.reduce((a, b) => a + b, 0) / slice.length : 0
}

interface Props {
  bars: number[]
  colorMode: string | null
}

export function SpectrumBars({ bars, colorMode }: Props) {
  const data = bars.length > 0 ? bars : Array(PLACEHOLDER_COUNT).fill(0)
  const n = data.length
  const isRgb = colorMode === 'spectrum_rgb'

  const bassHi   = Math.floor(BASS_END * n)
  const midHi    = Math.floor(MID_END * n)
  const bassAvg  = isRgb ? sliceAvg(data, 0, bassHi) : 0
  const midAvg   = isRgb ? sliceAvg(data, bassHi, midHi) : 0
  const trebleAvg = isRgb ? sliceAvg(data, midHi, n) : 0

  return (
    <div>
      <div className="relative h-20 flex items-end gap-px">
        {data.map((v, i) => (
          <div
            key={i}
            className="flex-1 rounded-sm"
            style={{
              height: `${Math.max(v * 100, 2)}%`,
              backgroundColor: isRgb ? BAND_COLORS[barBand(i, n)] : 'hsl(var(--accent))',
              opacity: Math.max(v, 0.18),
              transition: 'height 33ms linear, opacity 33ms linear',
            }}
          />
        ))}

        {isRgb && [bassHi / n, midHi / n].map((pos) => (
          <div
            key={pos}
            className="absolute inset-y-0 w-px bg-white/20 pointer-events-none"
            style={{ left: `${pos * 100}%` }}
          />
        ))}
      </div>

      {isRgb && (
        <div className="flex justify-between mt-1 text-xs font-mono">
          <span style={{ color: BAND_COLORS.bass }}>Bass {bassAvg.toFixed(2)}</span>
          <span style={{ color: BAND_COLORS.mid }}>Mid {midAvg.toFixed(2)}</span>
          <span style={{ color: BAND_COLORS.treble }}>Treble {trebleAvg.toFixed(2)}</span>
        </div>
      )}
    </div>
  )
}
