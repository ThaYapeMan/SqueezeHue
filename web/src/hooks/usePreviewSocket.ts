import { useEffect, useRef, useState } from 'react'

export interface SocketStatus {
  active_profile_id: string | null
  sync_master: string | null
  applied_delay_ms: number
  latency_warning: string | null
  processes: { squeezelite: boolean; cava: boolean }
  bridge_connected: boolean
  color_mode: string | null
}

export interface PreviewState {
  colour: { r: number; g: number; b: number }
  onset: boolean
  bars: number[]
  status: SocketStatus | null
  connected: boolean
  reconnectAttempt: number
}

const INITIAL_STATE: PreviewState = {
  colour: { r: 0, g: 0, b: 0 },
  onset: false,
  bars: [],
  status: null,
  connected: false,
  reconnectAttempt: 0,
}

export function usePreviewSocket(): PreviewState {
  const [state, setState] = useState<PreviewState>(INITIAL_STATE)
  const onsetTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let stopped = false

    function connect() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${location.host}/ws/preview`)

      ws.onopen = () => {
        attempt = 0
        setState((s) => ({ ...s, connected: true, reconnectAttempt: 0 }))
      }

      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false, reconnectAttempt: attempt }))
        if (!stopped) {
          // Exponential backoff: 250 ms → 500 → 1 s → 2 s → 4 s, cap at 5 s.
          const delay = Math.min(250 * Math.pow(2, attempt), 5000)
          attempt++
          reconnectTimer = setTimeout(connect, delay)
        }
      }

      ws.onerror = () => {
        ws?.close()
      }

      ws.onmessage = (event: MessageEvent) => {
        const msg = JSON.parse(event.data as string) as Record<string, unknown>

        if (msg.type === 'frame') {
          const c = msg.colour as { r: number; g: number; b: number }
          // Values arrive as 16-bit (0–65535); normalise to 0–1 for CSS.
          const colour = { r: c.r / 65535, g: c.g / 65535, b: c.b / 65535 }
          const onset = msg.onset as boolean

          setState((s) => ({ ...s, colour, onset: onset || s.onset }))

          if (onset) {
            if (onsetTimer.current) clearTimeout(onsetTimer.current)
            onsetTimer.current = setTimeout(
              () => setState((s) => ({ ...s, onset: false })),
              80
            )
          }
        } else if (msg.type === 'spectrum') {
          setState((s) => ({ ...s, bars: msg.bars as number[] }))
        } else if (msg.type === 'status') {
          const { type: _ignored, ...fields } = msg
          setState((s) => ({ ...s, status: fields as unknown as SocketStatus }))
        }
      }
    }

    connect()

    return () => {
      stopped = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (onsetTimer.current) clearTimeout(onsetTimer.current)
      ws?.close()
    }
  }, [])

  return state
}
