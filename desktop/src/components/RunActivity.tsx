import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, ChevronRight, LoaderCircle, Sparkles, Wrench } from 'lucide-react'
import type { Run, RunEvent } from '../types'

interface ToolActivity {
  id: string
  name: string
  input: string
  output: string
  resultState: string | null
  resultFinished: boolean
}

interface ActivityStep {
  seq: number
  modelCall: boolean
  model: string | null
  publicText: string
  tools: ToolActivity[]
}

interface ActivitySummary {
  steps: ActivityStep[]
  coverageGaps: number
  toolCount: number
}

const activeStatuses = new Set(['queued', 'pending', 'running', 'in_progress'])

function stringField(event: RunEvent, key: string): string {
  const value = event.payload[key]
  return typeof value === 'string' ? value : ''
}

/** Build display steps from public events only; SDK-internal reasoning is never reconstructed. */
export function summarizeActivity(events: RunEvent[]): ActivitySummary {
  const steps: ActivityStep[] = []
  const toolsById = new Map<string, ToolActivity>()
  let current: ActivityStep | null = null
  let coverageGaps = 0

  function ensureStep(seq: number): ActivityStep {
    if (!current) {
      current = { seq, modelCall: false, model: null, publicText: '', tools: [] }
      steps.push(current)
    }
    return current
  }

  function ensureTool(event: RunEvent): ToolActivity {
    const step = ensureStep(event.seq)
    const id = stringField(event, 'tool_call_id') || `event-${event.seq}`
    let tool = toolsById.get(id)
    if (!tool || (event.kind === 'tool_call_started' && tool.resultFinished)) {
      tool = { id, name: '', input: '', output: '', resultState: null, resultFinished: false }
      step.tools.push(tool)
      toolsById.set(id, tool)
    }
    const name = stringField(event, 'tool_call_name')
    if (name) tool.name = name
    return tool
  }

  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    if (event.kind === 'coverage_gap') coverageGaps += 1
    if (event.kind === 'model_call_started') {
      current = { seq: event.seq, modelCall: true, model: stringField(event, 'model_name') || null, publicText: '', tools: [] }
      steps.push(current)
    } else if (event.kind === 'text_delta') {
      ensureStep(event.seq).publicText += stringField(event, 'delta')
    } else if (event.kind === 'tool_call_started' || event.kind === 'tool_result_started') {
      ensureTool(event)
    } else if (event.kind === 'tool_call_delta') {
      ensureTool(event).input += stringField(event, 'delta')
    } else if (event.kind === 'tool_result_delta') {
      ensureTool(event).output += stringField(event, 'delta')
    } else if (event.kind === 'tool_result_finished') {
      const tool = ensureTool(event)
      tool.resultFinished = true
      tool.resultState = stringField(event, 'state') || null
    }
  }

  return { steps, coverageGaps, toolCount: steps.reduce((total, step) => total + step.tools.length, 0) }
}

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

function toolStatus(tool: ToolActivity, active: boolean): string {
  if (!tool.resultFinished) return active ? '执行中' : '结果未记录'
  return tool.resultState && !['success', 'completed', 'ok'].includes(tool.resultState.toLowerCase()) ? '执行失败' : '已完成'
}

function stepTitle(step: ActivityStep, index: number): string {
  return step.modelCall ? `第 ${index + 1} 次模型调用` : '其他公开活动'
}

function stepSummary(step: ActivityStep, run: Run): string {
  if (step.tools.length) return `工具：${step.tools.map((tool) => tool.name || '未命名').join('、')}`
  if (step.publicText) return '生成回复'
  return step.model ?? run.model ?? '模型未记录'
}

interface RunActivityProps {
  run: Run
  /** The dedicated trace view starts at step level; chat remains one line after completion. */
  defaultExpanded?: boolean
  onShowRaw?: () => void
}

export default function RunActivity({ run, defaultExpanded = false, onShowRaw }: RunActivityProps) {
  const active = activeStatuses.has(run.status)
  const [expanded, setExpanded] = useState(active || defaultExpanded)
  const [now, setNow] = useState(() => Date.now())
  const summary = useMemo(() => summarizeActivity(run.events), [run.events])

  useEffect(() => {
    // A finished turn collapses by default even if the user watched its live steps.
    setExpanded(active || defaultExpanded)
  }, [active, defaultExpanded, run.id])
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])

  const status = active ? '正在执行' : run.status === 'completed' ? '已完成' : run.status === 'failed' ? '运行失败' : run.status === 'interrupted' ? '已中断' : '运行未完成'
  const count = summary.steps.length
    ? `${summary.steps.filter((step) => step.modelCall).length} 次模型调用${summary.toolCount ? ` · ${summary.toolCount} 次工具调用` : ''}`
    : active ? '等待事件' : `${run.events.length} 条公开事件`

  return (
    <section className={`run-activity ${active ? 'is-running' : ''}`} aria-label="本轮公开执行过程">
      <button type="button" className="activity-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <span className="activity-main-icon" aria-hidden="true">{active ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}</span>
        <span className="activity-duration">{active ? status : durationLabel(run, now)}</span>
        <span className="activity-overview">{active ? `${durationLabel(run, now)} · ${count}` : `${status} · ${count}`}</span>
        <ChevronRight className="activity-chevron" size={15} aria-hidden="true" />
      </button>
      {expanded && <div className="activity-content">
        {summary.steps.length === 0 ? <p className="activity-empty">{active ? '正在等待公开执行事件…' : '本轮没有可分组的模型或工具事件。'}</p> : (
          <ol className="activity-step-list">
            {summary.steps.map((step, index) => (
              <li key={step.seq} className="activity-step-item">
                <details className="activity-step" open={active && index === summary.steps.length - 1 ? true : undefined}>
                  <summary>
                    <span className="activity-step-marker" aria-hidden="true">{index + 1}</span>
                    <span className="activity-step-title">{stepTitle(step, index)}</span>
                    <span className="activity-step-meta">{stepSummary(step, run)}</span>
                    <ChevronRight size={14} aria-hidden="true" />
                  </summary>
                  <div className="activity-step-body">
                    {step.publicText && (step.tools.length > 0 || (active && index < summary.steps.length - 1)) && (
                      <div className="activity-public-text"><span>公开文字</span><p>{step.publicText}</p></div>
                    )}
                    {step.tools.map((tool, toolIndex) => (
                      <details className="activity-tool" key={`${tool.id}-${toolIndex}`}>
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
                    {!step.tools.length && !step.publicText && <span className="activity-no-detail">这一调用没有公开文字或工具事件。</span>}
                    {!step.tools.length && step.publicText && index === summary.steps.length - 1 && !active && <span className="activity-no-detail">最终回复见下方。</span>}
                  </div>
                </details>
              </li>
            ))}
          </ol>
        )}
        {summary.coverageGaps > 0 && <div className="activity-gap"><AlertCircle size={13} />有 {summary.coverageGaps} 个框架事件暂未映射，原始记录中可查看类型。</div>}
        {onShowRaw && <button type="button" className="activity-raw-link" onClick={onShowRaw}>查看完整公开事件</button>}
      </div>}
    </section>
  )
}
