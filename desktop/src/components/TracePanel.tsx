import { Activity, AlertCircle, ArrowDown, Check, Circle, Wrench } from 'lucide-react'
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
  if (kind.includes('tool')) return <Wrench size={14} strokeWidth={1.8} />
  if (kind.includes('failed') || kind.includes('gap')) return <AlertCircle size={14} strokeWidth={1.8} />
  if (kind.includes('finished')) return <Check size={14} strokeWidth={1.8} />
  if (kind.includes('started')) return <Circle size={13} strokeWidth={1.8} />
  return <ArrowDown size={14} strokeWidth={1.8} />
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

interface TracePanelProps {
  run: Run | null
  onClose?: () => void
}

export default function TracePanel({ run, onClose }: TracePanelProps) {
  const events = [...(run?.events ?? [])].sort((a, b) => a.seq - b.seq)
  const safeErrorType = run?.error_type && /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(run.error_type) ? run.error_type : null

  return (
    <aside className="trace-panel" aria-label="公开执行轨迹">
      <div className="trace-heading">
        <div className="trace-heading-main">
          <span className="trace-title-icon"><Activity size={17} /></span>
          <div>
            <h2>执行轨迹</h2>
            <span>本次运行的公开事件</span>
          </div>
        </div>
        {onClose && <button className="icon-button trace-close" onClick={onClose} aria-label="关闭执行轨迹">×</button>}
      </div>

      {!run ? (
        <div className="trace-empty">
          <div className="trace-empty-icon"><Activity size={21} strokeWidth={1.5} /></div>
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
              {events.map((event) => (
                <li className={`event-item ${event.kind.includes('failed') || event.kind.includes('gap') ? 'is-warning' : ''}`} key={`${event.seq}-${event.kind}`}>
                  <span className="event-line" aria-hidden="true" />
                  <span className="event-icon" aria-hidden="true">{iconFor(event.kind)}</span>
                  <div className="event-content">
                    <div className="event-row">
                      <span className="event-name">{eventNames[event.kind] ?? event.kind}</span>
                      <span className="event-seq">#{event.seq}</span>
                    </div>
                    {typeof event.payload.tool_call_id === 'string' && <span className="event-tool-id">调用 ID：{event.payload.tool_call_id}</span>}
                    {eventDetail(event) && <pre className="event-payload">{eventDetail(event)}</pre>}
                    <details className="raw-event"><summary>查看原始公开事件</summary><pre>{JSON.stringify(event, null, 2)}</pre></details>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </aside>
  )
}
