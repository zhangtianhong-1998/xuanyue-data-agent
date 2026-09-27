import { ArrowUpRight, Bot, Clock3, UserRound } from 'lucide-react'
import type { Run } from '../types'

function displayTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(date)
}

interface RunCardProps {
  run: Run
  selected: boolean
  onSelect: () => void
}

export default function RunCard({ run, selected, onSelect }: RunCardProps) {
  const active = ['queued', 'pending', 'running', 'in_progress'].includes(run.status)

  return (
    <div className="run-pair">
      <div className="message user-message">
        <div className="message-avatar user-avatar"><UserRound size={16} strokeWidth={1.9} /></div>
        <div className="message-body">
          <div className="message-top"><strong>你</strong><span>{displayTime(run.created_at)}</span></div>
          <p>{run.question}</p>
        </div>
      </div>
      <div className="message assistant-message">
        <div className="message-avatar assistant-avatar"><Bot size={17} strokeWidth={1.8} /></div>
        <div className="message-body">
          <div className="message-top"><strong>玄月</strong><span>{run.kernel}</span></div>
          {run.answer ? (
            <div className="answer-text">{run.answer}</div>
          ) : active ? (
            <p className="muted-answer"><span className="typing-dot" />正在处理，执行事件会显示在右侧。</p>
          ) : run.status === 'failed' ? (
            <p className="failed-answer">本次运行失败。详情见执行轨迹。</p>
          ) : run.status === 'interrupted' ? (
            <p className="failed-answer">服务退出，本次运行已中断；可发起新一轮。</p>
          ) : run.status === 'incomplete' ? (
            <p className="failed-answer">本次运行未得到完整答复。详情见执行轨迹。</p>
          ) : (
            <p className="muted-answer">本次运行没有返回文本回复。</p>
          )}
          <button className={`trace-link ${selected ? 'selected' : ''}`} onClick={onSelect}>
            <Clock3 size={14} /> 查看执行轨迹 <ArrowUpRight size={13} />
          </button>
        </div>
      </div>
    </div>
  )
}
