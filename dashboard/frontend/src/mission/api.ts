// 대시보드 API 호출: /api/missions (미션 저장), /api/drones (드론 명령)

import type { Command, CommandAck, MissionPlan, MissionSummary } from '../protocol.ts'

export class ApiError extends Error {}

interface ValidationDetail {
  loc: (string | number)[]
  msg: string
}

/** FastAPI 오류 응답 → 읽을 수 있는 문장 */
function describe(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return (detail as ValidationDetail[])
      .map((d) => {
        const msg = d.msg.replace(/^Value error, /, '')
        const i = d.loc.indexOf('items')
        const n = i >= 0 ? d.loc[i + 1] : undefined
        return typeof n === 'number' ? `${n + 1}번 항목: ${msg}` : msg
      })
      .join(' / ')
  }
  return '알 수 없는 오류'
}

async function request<T>(method: string, url: string, body?: unknown, asText = false): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('대시보드 API에 연결할 수 없어요')
  }
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      detail = (await res.json()).detail
    } catch {
      /* 본문이 JSON이 아님 */
    }
    throw new ApiError(describe(detail))
  }
  if (res.status === 204) return undefined as T
  return (asText ? await res.text() : await res.json()) as T
}

export const missionsApi = {
  list: () => request<MissionSummary[]>('GET', '/api/missions'),
  get: (id: string) => request<MissionPlan>('GET', `/api/missions/${encodeURIComponent(id)}`),
  create: (plan: MissionPlan) => request<MissionPlan>('POST', '/api/missions', plan),
  update: (id: string, plan: MissionPlan) => request<MissionPlan>('PUT', `/api/missions/${encodeURIComponent(id)}`, plan),
  remove: (id: string) => request<void>('DELETE', `/api/missions/${encodeURIComponent(id)}`),
  importWaypoints: (name: string, text: string) =>
    request<MissionPlan>('POST', '/api/missions/waypoints/import', { name, text }),
  exportWaypoints: (plan: MissionPlan) => request<string>('POST', '/api/missions/waypoints/export', plan, true),
}

export function newCmdId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID().replaceAll('-', '')
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('')
}

export const dronesApi = {
  /** 명령을 보내고 드론의 마지막 ack(succeeded/failed)를 기다린다. 진행률은 /ws의 ack로 따라간다 */
  command: (droneId: string, cmdId: string, cmd: Command) =>
    request<CommandAck>('POST', `/api/drones/${encodeURIComponent(droneId)}/commands`, { cmd_id: cmdId, ...cmd }),
}
