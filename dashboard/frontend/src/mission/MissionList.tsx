import type { MissionSummary } from '../protocol.ts'

interface Props {
  missions: MissionSummary[] | null
  error: string | null
  currentId: string | null
  onOpen: (id: string) => void
  onDelete: (m: MissionSummary) => void
}

const dateFmt = new Intl.DateTimeFormat('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

export default function MissionList({ missions, error, currentId, onOpen, onDelete }: Props) {
  if (error) return <p className="err-text pad">{error}</p>
  if (!missions) return <p className="muted pad">불러오는 중…</p>
  if (!missions.length) return <p className="muted pad">저장된 미션이 없어요.</p>
  return (
    <ul className="mission-list">
      {missions.map((m) => (
        <li key={m.mission_id} className={m.mission_id === currentId ? 'current' : ''}>
          <button className="link" onClick={() => onOpen(m.mission_id)}>
            <strong>{m.name}</strong>
            <span className="muted small">
              웨이포인트 {m.waypoint_count}개 · {dateFmt.format(new Date(m.updated_at))}
            </span>
          </button>
          <button className="icon" title="삭제" aria-label={`${m.name} 삭제`} onClick={() => onDelete(m)}>
            ✕
          </button>
        </li>
      ))}
    </ul>
  )
}
