import { useEffect, useRef, useState } from 'react'

import { dronesApi, newCmdId } from '../mission/api.ts'
import type { Command, CommandAck } from '../protocol.ts'

export interface Notice {
  kind: 'ok' | 'err'
  text: string
}

export interface Running {
  cmdId: string
  droneId: string
  label: string
}

/**
 * 드론 명령 하나를 보내고 결과를 알림으로 보여 준다. 한 번에 하나씩.
 * 진행률은 /ws로 오는 ack(acks[cmdId])에서 읽는다.
 */
export function useDroneCommand(acks: Record<string, CommandAck>) {
  const [running, setRunning] = useState<Running | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(() => setNotice(null), notice.kind === 'ok' ? 5000 : 15000)
    return () => window.clearTimeout(t)
  }, [notice])

  /** 성공하면 ack, 실패하면 null. 알림은 여기서 띄운다 (okText가 null이면 성공 알림 생략) */
  const run = async (droneId: string, cmd: Command, label: string, okText: string | null = `${label} 완료`) => {
    const cmdId = newCmdId()
    setRunning({ cmdId, droneId, label })
    setNotice(null)
    try {
      const ack = await dronesApi.command(droneId, cmdId, cmd)
      if (!alive.current) return null
      if (ack.status === 'succeeded') {
        if (okText) setNotice({ kind: 'ok', text: okText })
        return ack
      }
      setNotice({ kind: 'err', text: `${label} 실패: ${ack.error ?? '이유를 알 수 없어요'}` })
    } catch (e) {
      if (alive.current) setNotice({ kind: 'err', text: `${label} 실패: ${e instanceof Error ? e.message : String(e)}` })
    } finally {
      if (alive.current) setRunning(null)
    }
    return null
  }

  const progress = running ? (acks[running.cmdId]?.progress ?? null) : null
  return { run, running, progress, notice, setNotice }
}
