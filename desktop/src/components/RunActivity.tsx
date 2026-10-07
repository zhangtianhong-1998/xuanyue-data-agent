import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, ChevronRight, LoaderCircle, Sparkles, Wrench } from 'lucide-react'
import type { Run } from '../types'
import { summarizeActivity, type ModelActivity, type ToolActivity } from './activitySummary'

const activeStatuses = new Set(['queued', 'pending', 'running', 'in_progress'])

function durationLabel(run: Run, now: number): string {
  const started = Date.parse(run.created_at)
  const ended = activeStatuses.has(run.status) ? now : Date.parse(run.updated_at)
  if (!Number.isFinite(started) || !Number.isFinite(ended)) return '用时未记录'
  const seconds = Math.max(0, Math.floor((ended - started) / 1000))
  if (seconds < 60) return `用时 ${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `用时 ${minutes} 分 ${seconds % 60} 秒`
  return `用时 ${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`
}

function modelStatus(model: ModelActivity, active: boolean): string {
  return model.finished ? '已完成' : active ? '执行中' : '结束未记录'
}

function toolStatus(tool: ToolActivity, active: boolean): string {
  if (!tool.resultFinished) return active ? tool.callFinished ? '等待返回' : '调用中' : '结果未记录'
  if (!tool.resultState) return '结果状态未记录'
  return ['success', 'completed', 'ok'].includes(tool.resultState.toLowerCase()) ? '已完成' : '执行失败'
}

interface RunActivityProps {
  run: Run
  /** The trace view opens the round; chat collapses it after completion. */
  defaultExpanded?: boolean
  onShowRaw?: () => void
}

export default function RunActivity({ run, defaultExpanded = false, onShowRaw }: RunActivityProps) {
  const active = activeStatuses.has(run.status)
  const [expanded, setExpanded] = useState(active || defaultExpanded)
  const [now, setNow] = useState(() => Date.now())
  const summary = useMemo(() => summarizeActivity(run.events), [run.events])

  useEffect(() => {
    // A finished chat turn collapses even when its live activity was expanded.
    setExpanded(active || defaultExpanded)
  }, [active, defaultExpanded, run.id])
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])

  const status = active ? '正在执行' : run.status === 'completed' ? '已完成' : run.status === 'failed' ? '运行失败' : run.status === 'interrupted' ? '已中断' : '运行未完成'
  let modelCount = `${summary.models.length} 次模型调用`
  if (summary.unlinked.length) {
    modelCount = summary.models.length ? `已关联 ${summary.models.length} 次模型调用` : '模型调用关系未核定'
  }
  const counts = [
    modelCount,
    summary.toolCount ? `${summary.unlinked.length ? '已关联 ' : ''}${summary.toolCount} 次工具调用` : '',
    summary.unlinked.length ? `${summary.unlinked.length} 条未关联事件` : '',
  ].filter(Boolean).join(' · ')
  const overview = active && !run.events.length ? '等待事件' : counts

  return (
    <section className={`run-activity ${active ? 'is-running' : ''}`} aria-label="本轮公开执行过程">
      <button type="button" className="activity-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <span className="activity-main-icon" aria-hidden="true">{active ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}</span>
        <span className="activity-duration">{active ? status : durationLabel(run, now)}</span>
        <span className="activity-overview">{active ? `${durationLabel(run, now)} · ${overview}` : `${status} · ${overview}`}</span>
        <ChevronRight className="activity-chevron" size={15} aria-hidden="true" />
      </button>
      {expanded && <div className="activity-content">
        {summary.models.length === 0 && <p className="activity-empty">{summary.unlinked.length ? '暂无可关联的模型调用；缺少关联 ID 的公开事件列在下方。' : active ? '正在等待模型调用事件…' : '本轮没有可关联的模型或工具事件。'}</p>}
        {summary.models.length > 0 && <ol className="activity-step-list">
          {summary.models.map((model, index) => (
            <li key={model.id} className="activity-step-item">
              <details className="activity-step" open={active && index === summary.models.length - 1 ? true : undefined}>
                <summary>
                  <span className="activity-step-marker" aria-hidden="true">{index + 1}</span>
                  <span className="activity-step-title">第 {index + 1} 次模型调用</span>
                  <span className="activity-step-meta">{model.model ?? run.model ?? '模型未记录'} · {modelStatus(model, active)}{model.tools.length ? ` · ${model.tools.length} 次工具调用` : ''}</span>
                  <ChevronRight size={14} aria-hidden="true" />
                </summary>
                <div className="activity-step-body">
                  {model.publicText && <div className="activity-public-text"><span>公开文字</span><p>{model.publicText}</p></div>}
                  {model.tools.map((tool) => (
                    <details className="activity-tool" key={tool.id}>
                      <summary>
                        <Wrench size={14} aria-hidden="true" />
                        <span className="activity-tool-name">{tool.name || '工具调用'}</span>
                        <span className="activity-tool-status">{toolStatus(tool, active)}</span>
                        <ChevronRight size={13} aria-hidden="true" />
                      </summary>
                      <div className="activity-tool-body">
                        {tool.input && <div><strong>输入</strong><pre>{tool.input}</pre></div>}
                        {tool.output && <div><strong>返回</strong><pre>{tool.output}</pre></div>}
                        {!tool.input && !tool.output && <span>尚无公开输入或返回内容。</span>}
                      </div>
                    </details>
                  ))}
                  {!model.tools.length && !model.publicText && <span className="activity-no-detail">这一调用没有公开文字或工具事件。</span>}
                </div>
              </details>
            </li>
          ))}
        </ol>}
        {summary.unlinked.length > 0 && <details className="activity-unlinked">
          <summary><AlertCircle size={13} />未关联公开事件（{summary.unlinked.length}）<ChevronRight size={13} aria-hidden="true" /></summary>
          <p>这些事件缺少有效关联，不能放进上方的模型或工具调用。</p>
          <ol>{summary.unlinked.map((event, index) => <li key={`${event.seq}-${index}`}>
            <span>#{event.seq} · {event.kind} · {event.reason}</span>
            {event.detail && <pre>{event.detail}</pre>}
          </li>)}</ol>
        </details>}
        {summary.coverageGaps > 0 && <div className="activity-gap"><AlertCircle size={13} />有 {summary.coverageGaps} 个框架事件暂未映射，原始记录中可查看类型。</div>}
        {onShowRaw && <button type="button" className="activity-raw-link" onClick={onShowRaw}>查看完整公开事件</button>}
      </div>}
    </section>
  )
}
