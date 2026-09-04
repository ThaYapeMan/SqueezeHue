import { Slider } from '@/components/ui/slider'

interface Props {
  label: string
  value: number
  min: number
  max: number
  step?: number
  unit?: string
  format?: (v: number) => string
  onChange: (v: number) => void
  disabled?: boolean
}

export function SliderField({ label, value, min, max, step = 1, unit, format, onChange, disabled }: Props) {
  const display = format ? format(value) : value.toString()

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-sm">{label}</span>
        <span className="font-mono text-sm tabular-nums w-20 text-right">
          {display}{unit ? ` ${unit}` : ''}
        </span>
      </div>
      <Slider
        min={min}
        max={max}
        step={step}
        value={[value]}
        onValueChange={([v]) => onChange(v)}
        disabled={disabled}
      />
    </div>
  )
}
