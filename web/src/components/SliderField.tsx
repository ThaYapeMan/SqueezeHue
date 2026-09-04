import { useEffect, useState } from 'react'
import { Input } from '@/components/ui/input'
import { Slider } from '@/components/ui/slider'

interface Props {
  label: string
  value: number
  min: number
  max: number
  step?: number
  format?: (v: number) => string
  onChange: (v: number) => void
  disabled?: boolean
  // Optional: show a numeric text input alongside the slider.
  // inputHz is the current Hz value displayed in the input; onInputCommit is
  // called with the clamped integer Hz value on Enter/blur.
  inputHz?: number
  inputMin?: number
  inputMax?: number
  onInputCommit?: (hz: number) => void
}

export function SliderField({
  label, value, min, max, step = 1, format, onChange, disabled,
  inputHz, inputMin = 20, inputMax = 20000, onInputCommit,
}: Props) {
  const hasInput = onInputCommit !== undefined

  // Local input text: syncs from inputHz when the slider moves, but lets the
  // user type freely without being reset until they commit or the Hz changes.
  const [inputText, setInputText] = useState<string>(
    hasInput ? String(inputHz ?? '') : ''
  )

  // When the slider moves (inputHz changes from outside), update the input
  // text. This fires on Reset/Apply but NOT while the user is mid-typing,
  // because typing does not change inputHz (the slider position is unchanged).
  useEffect(() => {
    if (hasInput && inputHz !== undefined) {
      setInputText(String(inputHz))
    }
  }, [hasInput, inputHz])

  function commitInput() {
    if (!onInputCommit) return
    const n = parseInt(inputText, 10)
    if (isNaN(n)) {
      // Revert to current Hz
      setInputText(String(inputHz ?? ''))
      return
    }
    const clamped = Math.min(inputMax, Math.max(inputMin, n))
    setInputText(String(clamped))
    onInputCommit(clamped)
  }

  const display = format ? format(value) : value.toString()

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-sm">{label}</span>
        {!hasInput && (
          <span className="font-mono text-sm tabular-nums w-20 text-right">
            {display}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Slider
          className="flex-1"
          min={min}
          max={max}
          step={step}
          value={[value]}
          onValueChange={([v]) => onChange(v)}
          disabled={disabled}
        />
        {hasInput && (
          <Input
            type="number"
            className="w-20 h-8 text-sm font-mono"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onBlur={commitInput}
            onKeyDown={(e) => { if (e.key === 'Enter') commitInput() }}
            disabled={disabled}
          />
        )}
      </div>
    </div>
  )
}
