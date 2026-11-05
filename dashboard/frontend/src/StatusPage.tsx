import type { LiveState } from './useLiveFeed.ts'

/** 서버·드론 연결 상태 표 */
export default function StatusPage({ live }: { live: LiveState }) {
  const { telemetry, drones, servers } = live
  const ids = Array.from(new Set([...Object.keys(drones), ...Object.keys(telemetry)])).sort()

  return (
    <main className="status-page">
      <section>
        <h2>서버</h2>
        {Object.keys(servers).length === 0 ? (
          <p className="muted">상태를 보고한 서버 없음</p>
        ) : (
          <ul className="servers">
            {Object.values(servers).map((s) => (
              <li key={s.name}>
                <span className={s.online ? 'dot ok' : 'dot bad'} /> {s.name}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>드론</h2>
        {ids.length === 0 ? (
          <p className="muted">
            드론 없음. <code>python sim/fake_drone.py</code> 또는 SITL + drone-agent를 실행하세요.
          </p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>상태</th>
                  <th>모드</th>
                  <th>위치</th>
                  <th>고도</th>
                  <th>속도</th>
                  <th>방향</th>
                  <th>배터리</th>
                  <th>GPS</th>
                </tr>
              </thead>
              <tbody>
                {ids.map((id) => {
                  const d = drones[id]
                  const t = telemetry[id]
                  return (
                    <tr key={id}>
                      <td>{id}</td>
                      <td>
                        <span className={d?.online ? 'dot ok' : 'dot bad'} /> {d?.online ? '온라인' : '오프라인'}
                      </td>
                      <td>{t ? `${t.flight.mode}${t.flight.armed ? ' · 시동' : ''}` : '-'}</td>
                      <td className="num">{t ? `${t.position.lat.toFixed(6)}, ${t.position.lon.toFixed(6)}` : '-'}</td>
                      <td className="num">{t ? `${t.position.alt_rel.toFixed(1)} m` : '-'}</td>
                      <td className="num">{t ? `${t.velocity.ground_speed.toFixed(1)} m/s` : '-'}</td>
                      <td className="num">{t ? `${t.velocity.heading.toFixed(0)}°` : '-'}</td>
                      <td className="num">
                        {t
                          ? `${t.battery.voltage.toFixed(2)} V${t.battery.remaining != null ? ` (${t.battery.remaining}%)` : ''}`
                          : '-'}
                      </td>
                      <td className="num">{t ? `${t.gps.fix_type >= 3 ? '3D' : '없음'} · ${t.gps.satellites}개` : '-'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  )
}
