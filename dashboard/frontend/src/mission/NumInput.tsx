import { useRef, useState } from 'react'

interface Props {
  value: number | null
  onCommit: (v: number | null) => void
  /** 표시할 소수 자릿수 (최대) */
  digits?: number
  allowEmpty?: boolean
  placeholder?: string
  className?: string
  title?: string
  'aria-label'?: string
}

function format(v: number | null, digits: number): string {
  if (v == null) return ''
  return String(Number(v.toFixed(digits)))
}

/**
 * 숫자 입력칸. 입력하는 동안은 글자 그대로 두고, Enter나 포커스를 잃을 때 한 번만 반영한다.
 * (한 글자마다 반영하면 실행 취소 기록이 지저분해지고, 입력 중간값으로 검증 오류가 깜빡인다)
 */
export default function NumInput({ value, onCommit, digits = 2, allowEmpty = false, ...rest }: Props) {
  const [text, setText] = useState<string | null>(null)
  const cancelled = useRef(false)

  const commit = () => {
    if (text == null) return
    const t = text.trim()
    setText(null)
    if (cancelled.current) {
      cancelled.current = false
      return
    }
    if (t === '') {
      if (allowEmpty && value != null) onCommit(null)
      return
    }
    const n = Number(t.replace(',', '.'))
    if (Number.isFinite(n) && n !== value) onCommit(n)
  }

  return (
    <input
      type="text"
      inputMode="decimal"
      autoComplete="off"
      {...rest}
      value={text ?? format(value, digits)}
      onFocus={(e) => {
        setText(format(value, digits))
        e.currentTarget.select()
      }}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') {
          cancelled.current = true
          e.currentTarget.blur()
        }
      }}
    />
  )
}
