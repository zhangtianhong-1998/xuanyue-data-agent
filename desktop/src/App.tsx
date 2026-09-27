import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  FolderClosed,
  LoaderCircle,
  Menu,
  MessageSquareText,
  Plus,
  Send,
  Sparkles,
  X,
} from 'lucide-react'
import { api } from './api'
import RunCard from './components/RunCard'
import TracePanel from './components/TracePanel'
import type { Bootstrap, Project, Session, SessionDetail } from './types'

const PROJECT_KEY = 'xuanyue.selectedProjectId'
const SESSION_KEY = 'xuanyue.selectedSessionId'
const activeStatuses = new Set(['queued', 'pending', 'running', 'in_progress'])

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '发生未知错误'
}

export default function App() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(() => localStorage.getItem(PROJECT_KEY))
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(() => localStorage.getItem(SESSION_KEY))
  const selectedSessionIdRef = useRef(selectedSessionId)
  const conversationRef = useRef<HTMLDivElement | null>(null)
  const followLatestRef = useRef(true)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const [panelError, setPanelError] = useState<string | null>(null)
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [sending, setSending] = useState(false)
  const [pendingRunId, setPendingRunId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [modal, setModal] = useState<'project' | 'session' | null>(null)
  const [formName, setFormName] = useState('')
  const [formKernel, setFormKernel] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [activeView, setActiveView] = useState<'chat' | 'trace'>('chat')
  const [sidebarOpen, setSidebarOpen] = useState(false)

  function chooseSession(id: string | null) {
    // 异步发送或加载返回时，以当前选中的会话为准，不能把旧会话内容写进新会话。
    // 项目列表刷新可能再次选中同一 ID；此时不能清空已有详情，因依赖 ID 的加载 effect 不会重跑。
    if (id === selectedSessionIdRef.current) return
    selectedSessionIdRef.current = id
    setSelectedSessionId(id)
    setDetail(null)
    setSelectedRunId(null)
    setDraft('')
    setPendingRunId(null)
    setActiveView('chat')
    followLatestRef.current = true
  }

  // 仅保存导航位置；项目、会话、运行与事件始终重新从本机 API 读取。
  useEffect(() => {
    if (selectedProjectId) localStorage.setItem(PROJECT_KEY, selectedProjectId)
    else localStorage.removeItem(PROJECT_KEY)
  }, [selectedProjectId])
  useEffect(() => {
    if (selectedSessionId) localStorage.setItem(SESSION_KEY, selectedSessionId)
    else localStorage.removeItem(SESSION_KEY)
  }, [selectedSessionId])

  useEffect(() => {
    const narrow = window.matchMedia('(max-width: 980px)')
    const onViewportChange = () => {
      // 抽屉与常驻侧栏切换时关闭旧状态，避免缩回窄屏后遮罩自行重现。
      setSidebarOpen(false)
    }
    narrow.addEventListener('change', onViewportChange)
    return () => narrow.removeEventListener('change', onViewportChange)
  }, [])

  useEffect(() => {
    let current = true
    api.bootstrap().then((data) => {
      if (!current) return
      setBootstrap(data)
      setProjects(data.projects)
      setFormKernel(data.kernels[0] ?? '')
      if (!data.projects.some((project) => project.id === selectedProjectId)) chooseSession(null)
      setSelectedProjectId((old) => data.projects.some((project) => project.id === old) ? old : (data.projects[0]?.id ?? null))
    }).catch((error: unknown) => {
      if (current) setBootError(errorMessage(error))
    })
    return () => { current = false }
  }, [])

  useEffect(() => {
    // 浏览器可能保存着旧项目 ID；启动时先核对目录，避免向服务请求已不存在的项目。
    if (!bootstrap || !selectedProjectId || !projects.some((item) => item.id === selectedProjectId)) {
      setSessions([])
      return
    }
    let current = true
    setLoadingSessions(true)
    setPanelError(null)
    api.listSessions(selectedProjectId).then(({ sessions: next }) => {
      if (!current) return
      setSessions(next)
      const previous = selectedSessionIdRef.current
      chooseSession(next.some((session) => session.id === previous) ? previous : (next[0]?.id ?? null))
    }).catch((error: unknown) => {
      if (current) setPanelError(errorMessage(error))
    }).finally(() => {
      if (current) setLoadingSessions(false)
    })
    return () => { current = false }
  }, [bootstrap, projects, selectedProjectId])

  useEffect(() => {
    if (!selectedSessionId) {
      setDetail(null)
      setSelectedRunId(null)
      return
    }
    let current = true
    setDetail(null)
    setSelectedRunId(null)
    setLoadingDetail(true)
    setPanelError(null)
    api.session(selectedSessionId).then((next) => {
      if (!current || selectedSessionIdRef.current !== selectedSessionId) return
      setDetail(next)
      setSelectedRunId((old) => next.runs.some((run) => run.id === old) ? old : (next.runs.at(-1)?.id ?? null))
    }).catch((error: unknown) => {
      if (current && selectedSessionIdRef.current === selectedSessionId) setPanelError(errorMessage(error))
    }).finally(() => {
      if (current) setLoadingDetail(false)
    })
    return () => { current = false }
  }, [selectedSessionId])

  const visibleDetail = detail?.session.id === selectedSessionId ? detail : null
  const activeRunIds = visibleDetail?.runs.filter((run) => activeStatuses.has(run.status)).map((run) => run.id).join('|') ?? ''
  useEffect(() => {
    if (!activeRunIds || !selectedSessionId) return
    let current = true
    let busy = false
    const runIds = activeRunIds.split('|')
    // 轮询只更新仍在运行的记录；历史会话重开时也能接续展示未结束的运行。
    const timer = window.setInterval(async () => {
      if (busy) return
      busy = true
      try {
        const updated = await Promise.all(runIds.map((id) => api.run(id)))
        if (!current || selectedSessionIdRef.current !== selectedSessionId) return
        setDetail((old) => old ? {
          ...old,
          runs: old.runs.map((run) => updated.find((next) => next.id === run.id) ?? run),
        } : old)
      } catch (error) {
        if (current && selectedSessionIdRef.current === selectedSessionId) setPanelError(errorMessage(error))
      } finally {
        busy = false
      }
    }, 400)
    return () => { current = false; window.clearInterval(timer) }
  }, [activeRunIds, selectedSessionId])

  const project = projects.find((item) => item.id === selectedProjectId) ?? null
  const session = visibleDetail?.session ?? sessions.find((item) => item.id === selectedSessionId) ?? null
  const modelStatus = selectedSessionId ? (visibleDetail?.model_status ?? null) : bootstrap?.model
  const selectedRun = visibleDetail?.runs.find((run) => run.id === selectedRunId) ?? null
  const sortedRuns = useMemo(() => [...(visibleDetail?.runs ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at)), [visibleDetail?.runs])
  const sessionBusy = Boolean(pendingRunId || visibleDetail?.runs.some((run) => activeStatuses.has(run.status)))
  const latestEventCount = sortedRuns.at(-1)?.events.length ?? 0

  useEffect(() => {
    // 只在用户仍停留在末尾时跟随新文字；主动上翻后不抢走阅读位置。
    const container = conversationRef.current
    if (activeView === 'chat' && container && followLatestRef.current) container.scrollTop = container.scrollHeight
  }, [activeView, latestEventCount, sortedRuns.length])

  function openModal(kind: 'project' | 'session') {
    setModal(kind)
    setFormName('')
    setFormError(null)
    setFormKernel(bootstrap?.kernels[0] ?? '')
  }

  function chooseProject(id: string) {
    if (id === selectedProjectId) {
      if (!selectedSessionId && sessions[0]) chooseSession(sessions[0].id)
    } else {
      setSessions([])
      chooseSession(null)
      setSelectedProjectId(id)
    }
    setSidebarOpen(false)
  }

  async function submitModal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = formName.trim()
    if (!name) { setFormError('请输入名称。'); return }
    setSaving(true)
    setFormError(null)
    try {
      if (modal === 'project') {
        const next = await api.createProject(name)
        setProjects((old) => [...old, next])
        setSessions([])
        chooseSession(null)
        setSelectedProjectId(next.id)
      } else if (modal === 'session' && selectedProjectId) {
        if (!formKernel) { setFormError('请选择主智能体内核。'); return }
        const next = await api.createSession(selectedProjectId, name, formKernel)
        setSessions((old) => [next, ...old])
        chooseSession(next.id)
      }
      setModal(null)
      setSidebarOpen(false)
    } catch (error) {
      setFormError(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  async function sendTurn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = draft.trim()
    if (!selectedSessionId || !modelStatus?.configured || !text || sending || sessionBusy) return
    const sessionId = selectedSessionId
    let acceptedRunId: string | null = null
    setSending(true)
    setPanelError(null)
    try {
      const { run_id } = await api.sendTurn(sessionId, text)
      acceptedRunId = run_id
      if (selectedSessionIdRef.current !== sessionId) return
      setPendingRunId(run_id)
      setDraft('')
      const next = await api.session(sessionId)
      if (!next.runs.some((run) => run.id === run_id)) next.runs.push(await api.run(run_id))
      if (selectedSessionIdRef.current !== sessionId) return
      setDetail(next)
      setSelectedRunId(run_id)
      setPendingRunId(null)
    } catch (error) {
      if (selectedSessionIdRef.current === sessionId) setPanelError(acceptedRunId ? `本轮已受理（运行 ID：${acceptedRunId}），但记录加载失败。请刷新会话查看，勿重复发送。` : errorMessage(error))
    } finally {
      setSending(false)
    }
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  if (bootError) return (
    <div className="boot-state">
      <span className="brand-symbol large">玄</span>
      <h1>无法连接本机服务</h1>
      <p>{bootError}</p>
      <p className="boot-hint">先启动 Python API，再刷新此窗口。</p>
      <button className="primary-button" onClick={() => window.location.reload()}>重新连接 <ArrowRight size={16} /></button>
    </div>
  )

  if (!bootstrap) return <div className="boot-state"><LoaderCircle className="spin" size={26} /><p>正在连接本机服务…</p></div>

  return (
    <div className={`app-shell ${sidebarOpen ? 'sidebar-open' : ''}`}>
      {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭项目导航" onClick={() => setSidebarOpen(false)} />}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <strong className="brand-name">玄月</strong>
          <button className="icon-button sidebar-close" aria-label="关闭项目导航" onClick={() => setSidebarOpen(false)}><X size={18} /></button>
        </div>
        <div className="sidebar-section-heading"><span>项目</span><button className="icon-button" title="新建项目" onClick={() => openModal('project')}><Plus size={17} /></button></div>
        <div className="project-list">
          {projects.length === 0 && <p className="sidebar-empty">还没有项目，点击 + 创建。</p>}
          {projects.map((item) => (
            <div key={item.id}>
              <button className={`project-item ${item.id === selectedProjectId ? 'active' : ''}`} onClick={() => chooseProject(item.id)}>
                {item.id === selectedProjectId ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <FolderClosed size={16} />
                <span>{item.name}</span>
              </button>
              {item.id === selectedProjectId && (
                <div className="session-group">
                  <button className="new-session" onClick={() => openModal('session')}><Plus size={14} /> 新建会话</button>
                  {loadingSessions && <div className="sidebar-loading"><LoaderCircle className="spin" size={14} /> 加载会话…</div>}
                  {!loadingSessions && sessions.length === 0 && <div className="sidebar-empty session-empty">这个项目还没有会话。</div>}
                  {sessions.map((item) => (
                    <button key={item.id} className={`session-item ${item.id === selectedSessionId ? 'active' : ''}`} onClick={() => { chooseSession(item.id); setSidebarOpen(false) }} title={item.title}>
                      <MessageSquareText size={15} /><span>{item.title}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </aside>

      <main className="main-pane">
        <header className="topbar">
          <div className="topbar-leading">
            <button className="icon-button mobile-nav-button" title="项目导航" aria-label="打开项目导航" onClick={() => setSidebarOpen(true)}><Menu size={20} /></button>
            <div className="title-stack"><span>{project?.name ?? '工作空间'}</span><h1>{session?.title ?? (project ? '选择或新建会话' : '欢迎使用玄月')}</h1></div>
          </div>
        </header>

        {selectedSessionId && <div className="view-bar">
          <div className="view-tabs" role="group" aria-label="会话视图">
            <button type="button" aria-pressed={activeView === 'chat'} className={activeView === 'chat' ? 'active' : ''} onClick={() => setActiveView('chat')}>对话</button>
            <button type="button" aria-pressed={activeView === 'trace'} className={activeView === 'trace' ? 'active' : ''} onClick={() => setActiveView('trace')}>执行轨迹</button>
          </div>
          <span className="session-kernel">主智能体：{session?.kernel ?? '读取中'}</span>
        </div>}

        {panelError && <div className="error-banner"><span>{panelError}</span><button className="icon-button" onClick={() => setPanelError(null)} aria-label="关闭错误"><X size={16} /></button></div>}

        {!project ? (
          <div className="main-empty"><span className="main-empty-icon"><FolderClosed size={21} strokeWidth={1.7} /></span><h1>从一个项目开始</h1><p>项目用于归拢会话；每个会话可选择自己的主智能体内核。</p><button className="primary-button" onClick={() => openModal('project')}><Plus size={16} /> 新建项目</button></div>
        ) : !selectedSessionId ? (
          <div className="main-empty"><span className="main-empty-icon"><MessageSquareText size={21} strokeWidth={1.7} /></span><h1>在「{project.name}」里开始对话</h1><p>新建会话时选择主智能体内核，之后就可以连续提问。</p><button className="primary-button" onClick={() => openModal('session')}><Plus size={16} /> 新建会话</button></div>
        ) : (
          <>
            {activeView === 'trace' ? (
              <div className="trace-view">
                <div className="trace-view-inner">
                  {sortedRuns.length > 1 && <div className="trace-run-picker"><label htmlFor="trace-run">查看轮次</label><select id="trace-run" value={selectedRunId ?? ''} onChange={(event) => setSelectedRunId(event.target.value)}>{sortedRuns.map((run, index) => <option key={run.id} value={run.id}>第 {index + 1} 轮 · {run.question}</option>)}</select></div>}
                  <TracePanel run={selectedRun} />
                </div>
              </div>
            ) : <>
              <div ref={conversationRef} onScroll={(event) => {
                const element = event.currentTarget
                followLatestRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100
              }} className={`conversation-scroll ${!loadingDetail && sortedRuns.length === 0 ? 'empty-conversation' : ''}`}>
                <div className="conversation-content">
                  {loadingDetail && !detail ? <div className="content-loading"><LoaderCircle className="spin" size={18} /> 加载会话记录…</div> : null}
                  {!loadingDetail && sortedRuns.length === 0 && <div className="conversation-welcome">
                    <div className="welcome-symbol"><Sparkles size={21} strokeWidth={1.7} /></div>
                    <span className="welcome-kicker">新的会话</span>
                    <h2>从一个问题开始</h2>
                    <p>描述你想了解的事，玄月会在当前会话中保留回复和公开执行轨迹。</p>
                  </div>}
                  {sortedRuns.map((run) => <RunCard key={run.id} run={run} selected={selectedRunId === run.id} onSelect={() => { setSelectedRunId(run.id); setActiveView('trace') }} />)}
                </div>
              </div>
              <div className="composer-area">
                {!modelStatus?.configured && <div className="composer-warning">{!modelStatus ? loadingDetail ? '正在读取会话模型状态…' : '未能读取会话模型状态，请刷新并检查本机服务。' : modelStatus.id ? `本会话绑定的 ${modelStatus.id} 模型不可用，请在本机恢复其配置。` : '模型尚未配置。请在本机配置模型后再发送消息。'}</div>}
                {sessionBusy && <div className="composer-warning">{pendingRunId ? '本轮已提交，正在同步运行记录；请勿重复发送。' : '本会话正在运行，请等待当前回复。'}</div>}
                <form className="composer" onSubmit={sendTurn}>
                  <textarea aria-label="输入消息" placeholder="向玄月提问…" rows={2} maxLength={20000} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={handleComposerKey} disabled={!modelStatus?.configured || sending || sessionBusy} />
                  <div className="composer-bottom"><span>Enter 发送 · Shift + Enter 换行</span><button className="send-button" title="发送消息" aria-label="发送消息" type="submit" disabled={!draft.trim() || !modelStatus?.configured || sending || sessionBusy}>{sending ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}</button></div>
                </form>
              </div>
            </>}
          </>
        )}
      </main>

      {modal && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setModal(null) }}>
        <form className="modal-card" onSubmit={submitModal}>
          <div className="modal-top"><span className="modal-icon">{modal === 'project' ? <FolderClosed size={18} strokeWidth={1.7} /> : <MessageSquareText size={18} strokeWidth={1.7} />}</span><button type="button" className="icon-button" onClick={() => setModal(null)} aria-label="关闭" disabled={saving}><X size={18} /></button></div>
          <h2>{modal === 'project' ? '新建项目' : '新建会话'}</h2>
          <p>{modal === 'project' ? '把相关的分析会话收在同一个项目里。' : `在「${project?.name ?? ''}」中开始一段新的连续对话。`}</p>
          <label htmlFor="new-name">{modal === 'project' ? '项目名称' : '会话名称'}</label>
          <input id="new-name" autoFocus maxLength={100} placeholder={modal === 'project' ? '例如：经营分析' : '例如：本周营收异常'} value={formName} onChange={(event) => setFormName(event.target.value)} />
          {modal === 'session' && <><label htmlFor="new-kernel">主智能体内核</label><select id="new-kernel" value={formKernel} onChange={(event) => setFormKernel(event.target.value)}>{bootstrap.kernels.map((kernel) => <option key={kernel} value={kernel}>{kernel}</option>)}</select><small className="field-help">当前会话使用所选内核运行；这里不会自动改用另一个内核。</small></>}
          {formError && <div className="form-error">{formError}</div>}
          <div className="modal-actions"><button className="secondary-button" type="button" onClick={() => setModal(null)} disabled={saving}>取消</button><button className="primary-button" type="submit" disabled={saving}>{saving ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />} 创建</button></div>
        </form>
      </div>}
    </div>
  )
}
