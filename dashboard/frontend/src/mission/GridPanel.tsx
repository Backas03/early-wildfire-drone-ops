import { formatDistance, formatDuration } from '../lib/geo.ts'
import { cameraSwath, type GridResult } from '../lib/grid.ts'
import NumInput from './NumInput.tsx'

export interface GridSettings {
  spacing: number
  /** null = 가장 긴 변 방향 자동 */
  angle: number | null
  alt: number
  /** null = 속도 변경 명령을 넣지 않음 */
  speed: number | null
}

interface Props {
  drawing: boolean
  vertexCount: number
  settings: GridSettings
  autoAngle: number
  result: GridResult
  /** 경로 시간 추정용 속도 (speed가 null이면 기본 속도) */
  cruiseSpeed: number
  onChange: (s: GridSettings) => void
  onToggleDrawing: () => void
  onApply: () => void
  onClear: () => void
}

export default function GridPanel(p: Props) {
  const s = p.settings
  const set = (patch: Partial<GridSettings>) => p.onChange({ ...s, ...patch })
  const speed = s.speed ?? p.cruiseSpeed

  return (
    <section className="panel">
      <div className="panel-head">
        <h3>순찰 경로 자동 생성</h3>
        <button className={p.drawing ? 'primary' : ''} onClick={p.onToggleDrawing}>
          {p.drawing ? '영역 그리기 끝' : p.vertexCount ? '영역 수정' : '영역 그리기'}
        </button>
      </div>

      {p.vertexCount < 3 ? (
        <p className="muted small">
          {p.drawing
            ? `지도를 클릭해 순찰할 영역의 꼭짓점을 찍으세요 (${p.vertexCount}/3개 이상).`
            : '순찰할 영역을 지도에 그리면 지그재그 경로를 만들어요.'}
        </p>
      ) : (
        <>
          <div className="form-grid">
            <label>
              줄 간격
              <span className="unit">
                <NumInput value={s.spacing} digits={1} onCommit={(v) => v && v >= 5 && set({ spacing: v })} />
                <span>m</span>
              </span>
            </label>
            <label>
              줄 방향
              <span className="unit">
                <NumInput
                  value={s.angle}
                  digits={0}
                  allowEmpty
                  placeholder={`자동 ${p.autoAngle}`}
                  onCommit={(v) => set({ angle: v == null ? null : ((Math.round(v) % 180) + 180) % 180 })}
                />
                <span>°</span>
              </span>
            </label>
            <label>
              고도
              <span className="unit">
                <NumInput value={s.alt} digits={1} onCommit={(v) => v && set({ alt: v })} />
                <span>m</span>
              </span>
            </label>
            <label>
              속도
              <span className="unit">
                <NumInput
                  value={s.speed}
                  digits={1}
                  allowEmpty
                  placeholder="기본"
                  onCommit={(v) => set({ speed: v })}
                />
                <span>m/s</span>
              </span>
            </label>
          </div>
          <p className="muted small">
            줄 방향은 방위각(0° = 남북, 90° = 동서)이고, 비우면 가장 긴 변을 따라가요. 고도 {s.alt} m에서 카메라가 아래를
            볼 때 촬영 폭은 약 {Math.round(cameraSwath(s.alt))} m예요 (Camera Module 3 표준 렌즈).
          </p>
          {p.result.points.length > 0 ? (
            <p className="small">
              줄 {p.result.lines}개 · 웨이포인트 {p.result.points.length}개 · 경로 {formatDistance(p.result.length)} · 약{' '}
              {formatDuration(p.result.length / speed)}
            </p>
          ) : (
            <p className="err-text">경로를 만들 수 없어요. 영역과 간격을 확인하세요.</p>
          )}
          <div className="row-buttons">
            <button className="primary" disabled={!p.result.points.length} onClick={p.onApply}>
              미션에 적용
            </button>
            <button onClick={p.onClear}>영역 지우기</button>
          </div>
        </>
      )}
    </section>
  )
}
