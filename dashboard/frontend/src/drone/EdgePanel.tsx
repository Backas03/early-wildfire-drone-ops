// 실시간 관제: 드론 카메라 미리보기와 엣지 AI(라즈베리파이) 탐지 결과
import type { CameraPreview, Detection, DetectionFrame, EdgeStatus, SnapshotInfo } from '../protocol.ts'
import { CLS_LABEL } from './camera.ts'

const STALE_MS = 5000
const SNAPSHOTS_SHOWN = 6

const ago = (now: number, ts: number) => {
  const s = Math.max(0, Math.round((now - ts) / 1000))
  return s <= 1 ? '방금' : s < 60 ? `${s}초 전` : `${Math.floor(s / 60)}분 전`
}
const clock = (ts: number) => new Date(ts).toLocaleTimeString('ko-KR', { hour12: false })

/** 사진 위에 탐지 상자를 겹쳐 그린다 (bbox는 0~1이라 크기와 상관없음) */
export function DetectionImage({ src, detections, alt }: { src: string; detections: Detection[]; alt: string }) {
  return (
    <div className="det-img">
      <img src={src} alt={alt} />
      {detections.map((d, i) => {
        const [x1, y1, x2, y2] = d.bbox
        return (
          <span
            key={i}
            className={`det-box ${d.cls}`}
            style={{ left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`, height: `${(y2 - y1) * 100}%` }}
          >
            <b>
              {CLS_LABEL[d.cls]} {Math.round(d.conf * 100)}%
            </b>
          </span>
        )
      })}
    </div>
  )
}

interface Props {
  status?: EdgeStatus
  preview?: CameraPreview
  frame?: DetectionFrame
  snapshots: SnapshotInfo[]
  now: number
}

export default function EdgePanel({ status, preview, frame, snapshots, now }: Props) {
  const working = !!status?.online && !status.detail && !!frame && now - frame.ts < STALE_MS
  const state = !status
    ? { text: '엣지 AI 없음', cls: 'bad' }
    : !status.online
      ? { text: '꺼짐', cls: 'bad' }
      : status.detail
        ? { text: '대기 중', cls: 'warn' }
        : working
          ? { text: '탐지 중', cls: 'ok' }
          : { text: '응답 없음', cls: 'bad' }
  const found = working ? frame.detections : []

  return (
    <section className="panel edge-panel">
      <div className="panel-head">
        <h3>카메라 · AI 탐지</h3>
        <span className={`pill ${state.cls}`}>{state.text}</span>
      </div>

      {!status ? (
        <p className="muted small">
          드론의 탐지 프로그램(edge-ai)이 연결되지 않았어요. 시뮬레이션은 <code>python edge-ai/edge.py --source sim</code>
        </p>
      ) : (
        <>
          {preview && now - preview.ts < STALE_MS * 2 ? (
            <DetectionImage
              src={`data:image/jpeg;base64,${preview.jpeg_b64}`}
              detections={preview.detections}
              alt="드론 카메라 미리보기"
            />
          ) : (
            <div className="det-img empty">{status.detail ?? '미리보기 없음'}</div>
          )}
          <p className="small edge-meta">
            {status.model ?? '모델?'} · {status.source === 'sim' ? '가상 카메라' : status.source}
            {status.fps != null && ` · 초당 ${status.fps.toFixed(1)}번`}
            {status.infer_ms != null && ` · 추론 ${Math.round(status.infer_ms)} ms`}
            {status.cpu_temp_c != null && (
              <span className={status.cpu_temp_c >= 80 ? 'bad-text' : status.cpu_temp_c >= 70 ? 'warn-text' : ''}>
                {' '}
                · CPU {status.cpu_temp_c.toFixed(0)}°C
              </span>
            )}
          </p>
          <p className="found">
            {found.length ? (
              found.slice(0, 4).map((d, i) => (
                <span key={i} className={`tag ${d.cls}`}>
                  {CLS_LABEL[d.cls]} {Math.round(d.conf * 100)}%
                </span>
              ))
            ) : (
              <span className="muted small">{working ? '지금 보이는 불·연기 없음' : '—'}</span>
            )}
          </p>
        </>
      )}

      {snapshots.length > 0 && (
        <>
          <h4>
            탐지 사진 <span className="muted small">서버에 저장됨 · 최근 {Math.min(snapshots.length, SNAPSHOTS_SHOWN)}장</span>
          </h4>
          <ul className="snapshots">
            {snapshots.slice(0, SNAPSHOTS_SHOWN).map((s) => (
              <li key={s.snapshot_id}>
                <a href={s.url} target="_blank" rel="noreferrer" title="원본 사진 열기">
                  <DetectionImage src={s.url} detections={s.detections} alt={`${clock(s.frame_ts)} 탐지 사진`} />
                </a>
                <span className="small">
                  {clock(s.frame_ts)} <span className="muted">({ago(now, s.frame_ts)})</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
