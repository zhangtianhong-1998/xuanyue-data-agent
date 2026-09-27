import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { Run } from '../types'
import { api } from '../api'
import RunActivity from './RunActivity'

function displayTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(date)
}

interface RunCardProps {
  run: Run
  projectId: string
  onSelect: () => void
}

function visibleDraft(run: Run): string {
  // 与后端答案组装保持一致：工具返回后，之前的模型文字不算最终回复。
  let fragments: string[] = []
  for (const event of run.events) {
    if (event.kind === 'tool_result_finished') fragments = []
    else if (event.kind === 'text_delta' && typeof event.payload.delta === 'string') fragments.push(event.payload.delta)
  }
  return fragments.join('')
}

export default function RunCard({ run, projectId, onSelect }: RunCardProps) {
  const [openImageId, setOpenImageId] = useState<string | null>(null)
  useEffect(() => {
    if (!openImageId) return
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setOpenImageId(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [openImageId])
  const active = ['queued', 'pending', 'running', 'in_progress'].includes(run.status)
  // 完成后只显示后端确认的答案，不再拼接事件文字，避免末段重复。
  const answer = run.answer || (active ? visibleDraft(run) : '')

  return (
    <div className="run-pair">
      <div className="message user-message">
        <div className="message-body">
          <div className="message-top"><strong>你</strong><span>{displayTime(run.created_at)}</span></div>
          <p>{run.question}</p>
          {projectId && run.attachments?.map((attachment) => (
            <button key={attachment.id} type="button" className="message-attachment" onClick={() => setOpenImageId(attachment.id)} aria-label="查看本轮上传的图片">
              <img src={api.attachmentUrl(projectId, attachment.id)} alt="本轮上传的图片" loading="lazy" />
            </button>
          ))}
        </div>
      </div>
      <div className="message assistant-message">
        <div className="message-body">
          <div className="message-top"><strong>玄月</strong><span>{run.kernel}</span></div>
          <RunActivity run={run} onShowRaw={onSelect} />
          {answer ? (
            <div className="answer-text">{answer}</div>
          ) : active ? (
            <p className="muted-answer">正在回复…</p>
          ) : run.status === 'failed' ? (
            <p className="failed-answer">本次运行失败。详情见执行轨迹。</p>
          ) : run.status === 'interrupted' ? (
            <p className="failed-answer">服务退出，本次运行已中断；可发起新一轮。</p>
          ) : run.status === 'incomplete' ? (
            <p className="failed-answer">本次运行未得到完整答复。详情见执行轨迹。</p>
          ) : (
            <p className="muted-answer">本次运行没有返回文本回复。</p>
          )}
        </div>
      </div>
      {openImageId && projectId && <div className="image-viewer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpenImageId(null) }}>
        <div className="image-viewer" role="dialog" aria-modal="true" aria-label="查看上传的图片">
          <button type="button" className="icon-button image-viewer-close" onClick={() => setOpenImageId(null)} aria-label="关闭图片"><X size={18} /></button>
          <img src={api.attachmentUrl(projectId, openImageId)} alt="本轮上传的图片大图" />
        </div>
      </div>}
    </div>
  )
}
