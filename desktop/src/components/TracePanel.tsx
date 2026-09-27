import { useState } from 'react'
import { AlertCircle, ArrowDown, Check, Circle, Wrench } from 'lucide-react'
import type { Run, RunEvent } from '../types'

const eventNames: Record<string, string> = {
  reply_started: '开始回复',
  reply_finished: '回复结束',
  model_call_started: '模型调用开始',
  model_call_finished: '模型调用结束',
  text_delta: '回复文本',
  tool_call_started: '工具调用开始',
  tool_call_delta: '工具输入',
  tool_call_finished: '工具调用完成',
  tool_result_started: '工具返回开始',
  tool_result_delta: '工具返回内容',
  tool_result_finished: '工具返回完成',
  coverage_gap: '轨迹覆盖缺口',
  run_failed: '运行失败',
}

function iconFor(kind: string) {
  if (kind.includes('tool')) return <Wrench size={12} strokeWidth={1.7} />
  if (kind.includes('failed') || kind.includes('gap')) return <AlertCircle size={12} strokeWidth={1.7} />
  if (kind.includes('finished')) return <Check size={12} strokeWidth={1.7} />
  if (kind.includes('started')) return <Circle size={11} strokeWidth={1.7} />
  return <ArrowDown size={12} strokeWidth={1.7} />
}

function eventDetail(event: RunEvent): string {
  const { payload } = event
  if (Object.keys(payload).length === 0) return ''
  const detail = Object.fromEntries(Object.entries(payload).filter(([key]) => key !== 'tool_call_id'))
  return Object.keys(detail).length ? JSON.stringify(detail, null, 2) : ''
}

function statusLabel(status: string): string {
  switch (status) {
    case 'running': return '正在运行'
    case 'completed': return '运行完成'
    case 'failed': return '运行失败'
    case 'incomplete': return '运行未完成'
    case 'interrupted': return '运行已中断'
    default: return status
  }
}

type TraceItem = { type: 'event'; event: RunEvent } | { type: 'text'; events: RunEvent[] }

function groupTextDeltas(events: RunEvent[]): TraceItem[] {
  const items: TraceItem[] = []
  for (const event of events) {
    const previous = items.at(-1)
    if (event.kind === 'text_delta') {
      if (previous?.type === 'text' && previous.events.at(-1)?.seq === event.seq - 1) {
        previous.events.push(event)
      } else {
        items.push({ type: 'text', events: [event] })
      }
    } else {
      items.push({ type: 'event', event })
    }
  }
  return items
}

function TextDeltaRow({ events }: { events: RunEvent[] }) {
  const [showText, setShowText] = useState(false)
  const [showRaw, setShowRaw] = useState(false)
  const first = events[0]
  const last = events[events.length - 1]
  const sequence = first.seq === last.seq ? `#${first.seq}` : `#${first.seq}–#${last.seq}`

  return (
    <li className="event-item">
      <span className="event-icon" aria-hidden="true">{iconFor('text_delta')}</span>
      <div className="event-content">
        <div className="event-row">
          <span className="event-name">回复文本</span>
          <span className="event-seq">{sequence}</span>
        </div>
        <span className="event-group-count">{events.length} 个文字片段</span>
        <details className="event-detail" onToggle={(event) => setShowText(event.currentTarget.open)}>
          <summary>查看回复文本</summary>
          {showText && <pre className="event-payload">{events.map((event) => typeof event.payload.delta === 'string' ? event.payload.delta : '').join('')}</pre>}
        </details>
        <details className="raw-event" onToggle={(event) => setShowRaw(event.currentTarget.open)}>
          <summary>查看原始公开事件</summary>
          {showRaw && <pre>{JSON.stringify(events, null, 2)}</pre>}
        </details>
      </div>
    </li>
  )
}

function EventRow({ event }: { event: RunEvent }) {
  const detail = eventDetail(event)
  return (
    <li className={`event-item ${event.kind.includes('failed') || event.kind.includes('gap') ? 'is-warning' : ''}`}>
      <span className="event-icon" aria-hidden="true">{iconFor(event.kind)}</span>
      <div className="event-content">
        <div className="event-row">
          <span className="event-name">{eventNames[event.kind] ?? event.kind}</span>
          <span className="event-seq">#{event.seq}</span>
        </div>
        {typeof event.payload.tool_call_id === 'string' && <span className="event-tool-id">调用 ID：{event.payload.tool_call_id}</span>}
        {detail && <details className="event-detail"><summary>查看事件内容</summary><pre className="event-payload">{detail}</pre></details>}
        <details className="raw-event"><summary>查看原始公开事件</summary><pre>{JSON.stringify(event, null, 2)}</pre></details>
      </div>
    </li>
  )
}

interface TracePanelProps {
  run: Run | null
}

export default function TracePanel({ run }: TracePanelProps) {
  const events = [...(run?.events ?? [])].sort((a, b) => a.seq - b.seq)
  // 折叠展示不改动事件本身；展开分组仍能逐条核对原始顺序和载荷。
  const items = groupTextDeltas(events)
  const safeErrorType = run?.error_type && /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(run.error_type) ? run.error_type : null

  return (
    <aside className="trace-panel" aria-label="公开执行轨迹">
      <div className="trace-heading">
        <div className="trace-heading-main">
          <div>
            <h2>执行轨迹</h2>
            <span>本次运行的公开事件</span>
          </div>
        </div>
      </div>

      {!run ? (
        <div className="trace-empty">
          <h3>暂无运行记录</h3>
          <p>发起一次对话后，可在这里查看模型调用、工具输入输出和错误。</p>
        </div>
      ) : (
        <>
          <div className="trace-run-meta">
            <div className="trace-meta-top">
              <span className={`status-dot ${run.status}`} />
              <strong>{statusLabel(run.status)}</strong>
              <span className="trace-event-count">{events.length} 条事件</span>
            </div>
            <div className="trace-meta-detail">
              <span>内核 <b>{run.kernel}</b></span>
              <span>模型 <b>{run.model ?? '未记录'}</b></span>
              {safeErrorType && <span>错误类型 <b>{safeErrorType}</b></span>}
            </div>
          </div>
          <div className="trace-note">事件按执行顺序排列；同一调用 ID 对应工具输入和返回。这里不包含模型隐藏推理。</div>
          {events.length === 0 ? (
            <div className="trace-waiting">这次运行还没有公开事件。</div>
          ) : (
            <ol className="event-list">
              {items.map((item) => item.type === 'text'
                ? <TextDeltaRow key={`text-${item.events[0].seq}`} events={item.events} />
                : <EventRow key={`${item.event.seq}-${item.event.kind}`} event={item.event} />)}
            </ol>
          )}
        </>
      )}
    </aside>
  )
}
