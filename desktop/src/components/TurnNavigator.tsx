import type { Run } from '../types'

interface Props {
  runs: Run[]
  activeRunId: string | null
  onNavigate: (runId: string) => void
}

/** 对话轮次的窄导航条；刻度按轮次排列，不暗示执行时长。 */
export default function TurnNavigator({ runs, activeRunId, onNavigate }: Props) {
  if (runs.length < 2) return null

  return <nav className="turn-navigator" aria-label="对话轮次导航">
    {runs.map((run, index) => <button
      key={run.id}
      type="button"
      className={run.id === activeRunId ? 'active' : ''}
      aria-current={run.id === activeRunId ? 'step' : undefined}
      aria-label={`跳到第 ${index + 1} 轮：${run.question.slice(0, 45)}`}
      title={`第 ${index + 1} 轮 · ${run.question.slice(0, 60)}`}
      onClick={() => onNavigate(run.id)}
    ><span /></button>)}
  </nav>
}
