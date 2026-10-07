import { useEffect, useState } from 'react'
import type { Run } from '../types'
import RunActivity from './RunActivity'

function statusLabel(status: string): string {
  switch (status) {
    case 'queued':
    case 'pending':
    case 'running': return '正在运行'
    case 'in_progress': return '正在运行'
    case 'completed': return '运行完成'
    case 'failed': return '运行失败'
    case 'incomplete': return '运行未完成'
    case 'interrupted': return '运行已中断'
    default: return status
  }
}

interface TracePanelProps {
  run: Run | null
}

export default function TracePanel({ run }: TracePanelProps) {
  const [showRaw, setShowRaw] = useState(false)
  useEffect(() => setShowRaw(false), [run?.id])
  const events = [...(run?.events ?? [])].sort((a, b) => a.seq - b.seq)
  const safeErrorType = run?.error_type && /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(run.error_type) ? run.error_type : null

  return (
    <aside className="trace-panel" aria-label="公开执行轨迹">
      <div className="trace-heading"><h2>执行轨迹</h2></div>
      {!run ? (
        <div className="trace-empty"><h3>暂无运行记录</h3><p>发起一次对话后，可在这里查看公开的模型和工具活动。</p></div>
      ) : (
        <>
          <div className="trace-run-meta">
            <div className="trace-meta-top"><span className={`status-dot ${run.status}`} /><strong>{statusLabel(run.status)}</strong><span className="trace-event-count">{events.length} 条事件</span></div>
            <div className="trace-meta-detail">
              <span>内核 <b>{run.kernel}</b></span>
              <span>模型 <b title={run.model ?? undefined}>{run.model_name || run.model || '未记录'}</b></span>
              {safeErrorType && <span>错误类型 <b>{safeErrorType}</b></span>}
            </div>
            {safeErrorType === 'UnsupportedReasoningContinuation' && <p className="trace-note">
              该模型要求当前尚未支持的推理续接。请在模型设置中选择支持关闭思考的档位，或换用兼容模型。
            </p>}
          </div>
          <RunActivity key={run.id} run={run} defaultExpanded />
          <details key={run.id} className="trace-raw-events" onToggle={(event) => setShowRaw(event.currentTarget.open)}>
            <summary>原始公开事件（{events.length}）</summary>
            {showRaw && <pre>{JSON.stringify(events, null, 2)}</pre>}
          </details>
          <p className="trace-note">这里记录公开事件，不包含模型隐藏推理。没有对应事件的技能或子智能体步骤不会显示。</p>
        </>
      )}
    </aside>
  )
}
