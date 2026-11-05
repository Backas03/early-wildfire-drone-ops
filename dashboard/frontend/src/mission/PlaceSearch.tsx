// 지도 장소 검색: 이름·주소(대시보드 API가 OpenStreetMap에 물음)나 좌표로 바로 이동한다.
// MapContainer 안에 넣는다. 지도 오른쪽 위, 배경 지도 선택 아래에 붙는다.

import L from 'leaflet'
import { useEffect, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { CircleMarker, Tooltip, useMap } from 'react-leaflet'

import { parseLatLon } from '../lib/geo.ts'

interface Place {
  name: string
  detail: string
  lat: number
  lon: number
  /** 남, 서, 북, 동 */
  bbox: [number, number, number, number] | null
}

interface Props {
  /** 검색해서 지도를 옮긴 뒤 부른다 (실시간 관제: 드론 따라가기를 끈다) */
  onJump?: () => void
}

// 지도 위 색은 배경 지도 위에서 잘 보이도록 테마와 관계없이 고정
const PIN_COLOR = '#2563eb'

/** "대구대학교, 진량읍, 경산시, 경상북도, 38454, 대한민국" → "진량읍 경산시 경상북도" */
function shortDetail(p: Place): string {
  return p.detail
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s && s !== p.name && s !== '대한민국' && !/^\d{5}$/.test(s))
    .slice(0, 3)
    .join(' ')
}

export default function PlaceSearch({ onJump }: Props) {
  const map = useMap()
  // Leaflet 컨트롤 칸에 React로 그린다. 여기서 누른 것·굴린 것은 지도로 넘기지 않는다
  // (미션 계획 화면에서 지도를 누르면 웨이포인트가 찍히므로).
  const [box] = useState(() => {
    const el = L.DomUtil.create('div', 'leaflet-control place-search')
    L.DomEvent.disableClickPropagation(el)
    L.DomEvent.disableScrollPropagation(el)
    return el
  })
  useEffect(() => {
    const ctl = new L.Control({ position: 'topright' })
    ctl.onAdd = () => box
    ctl.addTo(map)
    return () => {
      ctl.remove()
    }
  }, [map, box])

  const [text, setText] = useState('')
  const [results, setResults] = useState<Place[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pin, setPin] = useState<Place | null>(null)

  const jump = (p: Place) => {
    if (p.bbox) {
      const [s, w, n, e] = p.bbox
      map.flyToBounds(
        [
          [s, w],
          [n, e],
        ],
        { maxZoom: 18, padding: [40, 40], duration: 0.8 },
      )
    } else {
      map.flyTo([p.lat, p.lon], Math.max(map.getZoom(), 17), { duration: 0.8 })
    }
    setPin(p)
    setResults(null)
    onJump?.()
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const q = text.trim()
    if (!q || busy) return
    setError(null)
    setResults(null)
    const ll = parseLatLon(q)
    if (ll) {
      jump({ ...ll, name: `${ll.lat.toFixed(5)}, ${ll.lon.toFixed(5)}`, detail: '', bbox: null })
      return
    }
    setBusy(true)
    try {
      const res = await fetch(`/api/places/search?q=${encodeURIComponent(q)}`)
      const body: unknown = await res.json().catch(() => null)
      if (!res.ok) {
        const detail = (body as { detail?: unknown } | null)?.detail
        throw new Error(typeof detail === 'string' ? detail : `검색 실패 (${res.status})`)
      }
      const found = body as Place[]
      if (found.length === 0) setError('찾지 못했어요. 다른 이름이나 좌표(위도, 경도)로 입력해 보세요.')
      else if (found.length === 1) jump(found[0])
      else setResults(found)
    } catch (err) {
      setError(err instanceof TypeError ? '대시보드 API에 연결하지 못했어요' : (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const clear = () => {
    setText('')
    setResults(null)
    setError(null)
    setPin(null)
  }

  return (
    <>
      {createPortal(
        <form className="place-search-form" role="search" onSubmit={submit}>
          <input
            type="search"
            value={text}
            placeholder="장소·주소·좌표로 이동"
            aria-label="장소 검색"
            title="예: 대구대학교, 경산시 진량읍, 35.8964, 128.8495 (Esc: 지우기)"
            onChange={(e) => {
              setText(e.target.value)
              if (!e.target.value) clear()
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') clear()
            }}
          />
          <button type="submit" disabled={busy || !text.trim()} title="이동 (Enter)">
            {busy ? '찾는 중…' : '이동'}
          </button>
          {error && <p className="place-msg">{error}</p>}
          {results && (
            <ul className="place-results" aria-label="검색 결과">
              {results.map((r) => (
                <li key={`${r.lat},${r.lon},${r.name}`}>
                  <button type="button" title={r.detail} onClick={() => jump(r)}>
                    <strong>{r.name}</strong>
                    <span>{shortDetail(r)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </form>,
        box,
      )}
      {pin && (
        <CircleMarker
          center={[pin.lat, pin.lon]}
          radius={7}
          pathOptions={{ color: '#ffffff', weight: 2, fillColor: PIN_COLOR, fillOpacity: 1 }}
          interactive={false}
        >
          <Tooltip permanent direction="right" offset={[8, 0]}>
            {pin.name}
          </Tooltip>
        </CircleMarker>
      )}
    </>
  )
}
