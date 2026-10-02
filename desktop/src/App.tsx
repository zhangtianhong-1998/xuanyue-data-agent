import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type FormEvent, type KeyboardEvent } from 'react'
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  FolderClosed,
  ImagePlus,
  LoaderCircle,
  Menu,
  MessageSquareText,
  MoreHorizontal,
  Pencil,
  Plus,
  Send,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { api } from './api'
import ModelSettings from './components/ModelSettings'
import SelectionPopover from './components/SelectionPopover'
import RunCard from './components/RunCard'
import TracePanel from './components/TracePanel'
import TurnNavigator from './components/TurnNavigator'
import type { Bootstrap, CatalogModel, Project, Session, SessionDetail } from './types'

const PROJECT_KEY = 'xuanyue.selectedProjectId'
const SESSION_KEY = 'xuanyue.selectedSessionId'
const CLEANUP_NOTICE_KEY = 'xuanyue.attachmentCleanupNotice'
const activeStatuses = new Set(['queued', 'pending', 'running', 'in_progress'])
const MAX_IMAGE_BYTES = 5 * 1024 * 1024
const MAX_IMAGES_PER_TURN = 4
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg'])

type DraftImage = { id: number; file: File; previewUrl: string }

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '发生未知错误'
}

function projectNameFromFolder(folderPath: string): string | null {
  // Electron 返回本机绝对路径；网页预览手填路径时也兼容 Windows 分隔符。
  const basename = folderPath.trim().replace(/[\\/]+$/, '').split(/[\\/]/).at(-1)?.trim()
  return basename && basename !== '.' && basename !== '..' ? basename : null
}

function catalogModels(bootstrap: Bootstrap | null): CatalogModel[] {
  if (!bootstrap) return []
  if (bootstrap.models) return bootstrap.models
  // 兼容没有模型目录的旧本机服务：仍允许选择原来的默认模型。
  return bootstrap.model.id ? [{ ...bootstrap.model, id: bootstrap.model.id }] : []
}

type ModalState =
  | { mode: 'create'; kind: 'project' | 'session' }
  | { mode: 'rename'; kind: 'project' | 'session'; id: string; originalName: string }

type DeleteTarget = { kind: 'project' | 'session'; id: string; name: string }
type ActionMenu = { kind: 'project' | 'session'; id: string }

export default function App() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(() => localStorage.getItem(PROJECT_KEY))
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(() => localStorage.getItem(SESSION_KEY))
  const selectedProjectIdRef = useRef(selectedProjectId)
  const selectedSessionIdRef = useRef(selectedSessionId)
  const deletedProjectIdsRef = useRef(new Set<string>())
  const deletedSessionIdsRef = useRef(new Set<string>())
  const conversationRef = useRef<HTMLDivElement | null>(null)
  const imageInputRef = useRef<HTMLInputElement | null>(null)
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null)
  const followLatestRef = useRef(true)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const [panelError, setPanelError] = useState<string | null>(null)
  const [cleanupNotice, setCleanupNotice] = useState<string | null>(() => localStorage.getItem(CLEANUP_NOTICE_KEY))
  const [loadingSessions, setLoadingSessions] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [sending, setSending] = useState(false)
  const [pendingRunId, setPendingRunId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [selectedImages, setSelectedImages] = useState<DraftImage[]>([])
  const selectedImagesRef = useRef<DraftImage[]>([])
  const nextImageIdRef = useRef(0)
  const [modal, setModal] = useState<ModalState | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null)
  const [actionMenu, setActionMenu] = useState<ActionMenu | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [formName, setFormName] = useState('')
  const [formWorkspacePath, setFormWorkspacePath] = useState('')
  const [selectingFolder, setSelectingFolder] = useState(false)
  const [formKernel, setFormKernel] = useState('')
  const [formModel, setFormModel] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const creatingProjectRef = useRef(false)
  const [activeView, setActiveView] = useState<'chat' | 'trace'>('chat')
  const [visibleRunId, setVisibleRunId] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [showModelSettings, setShowModelSettings] = useState(false)

  function updateSelectedImages(next: DraftImage[]) {
    // 同一张图片可能跨多次选择保留；只释放真正移除的预览 URL。
    const previous = selectedImagesRef.current
    selectedImagesRef.current = next
    setSelectedImages(next)
    for (const image of previous) {
      if (!next.some((item) => item.id === image.id)) URL.revokeObjectURL(image.previewUrl)
    }
  }

  function chooseSession(id: string | null) {
    // 异步发送或加载返回时，以当前选中的会话为准，不能把旧会话内容写进新会话。
    // 项目列表刷新可能再次选中同一 ID；此时不能清空已有详情，因依赖 ID 的加载 effect 不会重跑。
    if (id === selectedSessionIdRef.current) return
    selectedSessionIdRef.current = id
    setSelectedSessionId(id)
    if (id) localStorage.setItem(SESSION_KEY, id)
    else localStorage.removeItem(SESSION_KEY)
    setDetail(null)
    setSelectedRunId(null)
    setVisibleRunId(null)
    setDraft('')
    updateSelectedImages([])
    setPendingRunId(null)
    setActiveView('chat')
    followLatestRef.current = true
  }

  async function refreshModelCatalog() {
    // 设置保存后重新读取本机服务的有效目录，也刷新旧会话的模型可用状态。
    try {
      const next = await api.bootstrap()
      setBootstrap(next)
      const models = catalogModels(next)
      setFormModel((current) => models.some((model) => model.id === current)
        ? current : (models.find((model) => model.id === next.model.id)?.id ?? models[0]?.id ?? ''))
      const sessionId = selectedSessionIdRef.current
      if (sessionId) {
        const refreshed = await api.session(sessionId)
        if (selectedSessionIdRef.current === sessionId) setDetail(refreshed)
      }
    } catch (error) {
      setPanelError(errorMessage(error))
    }
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

  useEffect(() => () => {
    // 关闭页面时释放仍留在草稿里的图片预览。
    for (const image of selectedImagesRef.current) URL.revokeObjectURL(image.previewUrl)
  }, [])

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
    if (!actionMenu) return
    // 菜单只属于当前条目；点击别处或按 Escape 时关闭，避免切换项目后仍盖在侧栏上。
    const closeOutside = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest('[data-sidebar-actions]')) setActionMenu(null)
    }
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setActionMenu(null)
    }
    document.addEventListener('pointerdown', closeOutside)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOutside)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [actionMenu])

  useEffect(() => {
    let current = true
    api.bootstrap().then((data) => {
      if (!current) return
      setBootstrap(data)
      setProjects(data.projects)
      setFormKernel(data.kernels[0] ?? '')
      const availableModels = catalogModels(data)
      setFormModel(
        availableModels.find((model) => model.id === data.model.id)?.id
          ?? availableModels[0]?.id
          ?? '',
      )
      if (!data.projects.some((project) => project.id === selectedProjectIdRef.current)) chooseSession(null)
      const initialProjectId = data.projects.some((project) => project.id === selectedProjectIdRef.current)
        ? selectedProjectIdRef.current : (data.projects[0]?.id ?? null)
      selectedProjectIdRef.current = initialProjectId
      setSelectedProjectId(initialProjectId)
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
    const projectId = selectedProjectId
    api.listSessions(projectId).then(({ sessions: next }) => {
      if (!current || selectedProjectIdRef.current !== projectId || deletedProjectIdsRef.current.has(projectId)) return
      // 删除可能先于旧列表请求完成；墓碑只在本次页面生命期内过滤迟到的响应。
      const remaining = next.filter((session) => !deletedSessionIdsRef.current.has(session.id))
      setSessions(remaining)
      const previous = selectedSessionIdRef.current
      chooseSession(remaining.some((session) => session.id === previous) ? previous : (remaining[0]?.id ?? null))
    }).catch((error: unknown) => {
      if (current && selectedProjectIdRef.current === projectId && !deletedProjectIdsRef.current.has(projectId)) {
        setPanelError(errorMessage(error))
      }
    }).finally(() => {
      if (current && selectedProjectIdRef.current === projectId) setLoadingSessions(false)
    })
    return () => { current = false }
  }, [bootstrap, projects, selectedProjectId])

  useEffect(() => {
    // 首次读完项目目录再读取会话，避免清空运行库后用浏览器旧 ID 请求出 404。
    if (!bootstrap || !selectedSessionId) {
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
      if (!current || selectedSessionIdRef.current !== selectedSessionId || deletedSessionIdsRef.current.has(selectedSessionId)) return
      setDetail(next)
      setSelectedRunId((old) => next.runs.some((run) => run.id === old) ? old : (next.runs.at(-1)?.id ?? null))
    }).catch((error: unknown) => {
      if (current && selectedSessionIdRef.current === selectedSessionId) setPanelError(errorMessage(error))
    }).finally(() => {
      if (current) setLoadingDetail(false)
    })
    return () => { current = false }
  }, [bootstrap, selectedSessionId])

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
        setDetail((old) => old?.session.id === selectedSessionId && selectedSessionIdRef.current === selectedSessionId ? {
          ...old,
          runs: old.runs.map((run) => updated.find((next) => next.id === run.id) ?? run),
        } : old)
        if (updated.some((run) => !activeStatuses.has(run.status))) {
          // 首轮标题可能由模型在结束前生成；Run 到终态后同步会话元数据和侧栏标题。
          const refreshed = await api.session(selectedSessionId)
          if (!current || selectedSessionIdRef.current !== selectedSessionId) return
          setDetail((old) => old?.session.id === selectedSessionId ? { ...old, session: refreshed.session, title_state: refreshed.title_state } : old)
          setSessions((old) => old.map((item) => item.id === refreshed.session.id ? refreshed.session : item))
        }
      } catch (error) {
        if (current && selectedSessionIdRef.current === selectedSessionId) setPanelError(errorMessage(error))
      } finally {
        busy = false
      }
    }, 400)
    return () => { current = false; window.clearInterval(timer) }
  }, [activeRunIds, selectedSessionId])

  const project = projects.find((item) => item.id === selectedProjectId) ?? null
  const titlePending = Boolean(visibleDetail && ['pending', 'generating'].includes(visibleDetail.title_state ?? '')
    && visibleDetail.runs.some((run) => run.status === 'completed'))
  useEffect(() => {
    if (!titlePending || !selectedSessionId) return
    let current = true
    let busy = false
    // 标题请求独立于主 Run；只在首轮完成且仍待命名时短轮询，不阻塞答复。
    const timer = window.setInterval(async () => {
      if (busy) return
      busy = true
      try {
        const refreshed = await api.session(selectedSessionId)
        if (!current || selectedSessionIdRef.current !== selectedSessionId) return
        setDetail((old) => old?.session.id === selectedSessionId ? {
          ...old,
          session: refreshed.session,
          title_state: refreshed.title_state,
        } : old)
        setSessions((old) => old.map((item) => item.id === refreshed.session.id ? refreshed.session : item))
      } catch {
        // 本机服务暂时不可用时保留占位标题；下次轮询或重开会话仍可恢复。
      } finally {
        busy = false
      }
    }, 1000)
    return () => { current = false; window.clearInterval(timer) }
  }, [titlePending, selectedSessionId])
  const session = visibleDetail?.session ?? sessions.find((item) => item.id === selectedSessionId) ?? null
  const modelStatus = selectedSessionId ? (visibleDetail?.model_status ?? null) : bootstrap?.model
  const modelOptions = catalogModels(bootstrap)
  const selectedFormModel = modelOptions.find((model) => model.id === formModel)
  const selectedRun = visibleDetail?.runs.find((run) => run.id === selectedRunId) ?? null
  const sortedRuns = useMemo(() => [...(visibleDetail?.runs ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at)), [visibleDetail?.runs])
  const sessionBusy = Boolean(pendingRunId || visibleDetail?.runs.some((run) => activeStatuses.has(run.status)))
  const deleteTargetIsBusy = Boolean(deleteTarget && (sending || sessionBusy) && (
    (deleteTarget.kind === 'session' && deleteTarget.id === selectedSessionId)
    || (deleteTarget.kind === 'project' && deleteTarget.id === selectedProjectId)
  ))
  const latestEventCount = sortedRuns.at(-1)?.events.length ?? 0

  useEffect(() => {
    // 只在用户仍停留在末尾时跟随新文字；主动上翻后不抢走阅读位置。
    const container = conversationRef.current
    if (activeView === 'chat' && container && followLatestRef.current) container.scrollTop = container.scrollHeight
  }, [activeView, latestEventCount, sortedRuns.length])

  function updateVisibleRun(container: HTMLDivElement) {
    const midpoint = container.getBoundingClientRect().top + container.clientHeight / 2
    let nearest: string | null = null
    for (const entry of container.querySelectorAll<HTMLElement>('[data-turn-id]')) {
      if (entry.getBoundingClientRect().top > midpoint) break
      nearest = entry.dataset.turnId ?? null
    }
    setVisibleRunId(nearest ?? container.querySelector<HTMLElement>('[data-turn-id]')?.dataset.turnId ?? null)
  }

  function navigateToRun(runId: string) {
    const container = conversationRef.current
    const entry = Array.from(container?.querySelectorAll<HTMLElement>('[data-turn-id]') ?? [])
      .find((element) => element.dataset.turnId === runId)
    if (!entry) return
    followLatestRef.current = false
    setVisibleRunId(runId)
    entry.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function openModal(kind: 'project' | 'session') {
    if (kind === 'project' && window.xuanyueDesktop) {
      // 桌面端直接选目录并创建，不让用户再填一次项目名或路径。
      void createProjectFromFolder()
      return
    }
    setModal({ mode: 'create', kind })
    setFormName('')
    setFormWorkspacePath('')
    setFormError(null)
    setFormKernel(bootstrap?.kernels[0] ?? '')
    setFormModel(
      modelOptions.find((model) => model.id === bootstrap?.model.id)?.id
        ?? modelOptions[0]?.id
        ?? '',
    )
  }

  function openRenameModal(kind: 'project' | 'session', id: string, originalName: string) {
    setActionMenu(null)
    setModal({ mode: 'rename', kind, id, originalName })
    setFormName(originalName)
    setFormError(null)
    setSidebarOpen(false)
  }

  function openDeleteModal(kind: 'project' | 'session', id: string, name: string) {
    setActionMenu(null)
    setDeleteTarget({ kind, id, name })
    setDeleteError(null)
    setSidebarOpen(false)
  }

  function activateCreatedProject(next: Project) {
    setProjects((old) => [...old, next])
    setSessions([])
    chooseSession(null)
    selectedProjectIdRef.current = next.id
    setSelectedProjectId(next.id)
    setSidebarOpen(false)
  }

  async function createProjectFromFolder() {
    if (creatingProjectRef.current) return
    const picker = window.xuanyueDesktop?.chooseProjectFolder
    if (!picker) return
    creatingProjectRef.current = true
    setSelectingFolder(true)
    setPanelError(null)
    try {
      const folderPath = await picker()
      if (!folderPath) return
      const name = projectNameFromFolder(folderPath)
      if (!name || name.length > 100) {
        setPanelError('文件夹名称无法用作项目名，请选择名称不超过 100 字的普通文件夹。')
        return
      }
      setSaving(true)
      activateCreatedProject(await api.createProject(name, folderPath))
    } catch (error) {
      setPanelError(errorMessage(error))
    } finally {
      setSelectingFolder(false)
      setSaving(false)
      creatingProjectRef.current = false
    }
  }

  function chooseProject(id: string) {
    setActionMenu(null)
    if (id === selectedProjectId) {
      if (!selectedSessionId && sessions[0]) chooseSession(sessions[0].id)
    } else {
      setSessions([])
      chooseSession(null)
      selectedProjectIdRef.current = id
      setSelectedProjectId(id)
    }
    setSidebarOpen(false)
  }

  function toggleActionMenu(kind: 'project' | 'session', id: string) {
    setActionMenu((current) => current?.kind === kind && current.id === id ? null : { kind, id })
  }

  function showCleanupNotice(notice: string) {
    // 附件待清理信息跨会话切换和页面刷新保留，直至用户明确关闭。
    setCleanupNotice(notice)
    localStorage.setItem(CLEANUP_NOTICE_KEY, notice)
  }

  async function confirmDelete(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!deleteTarget || deleting || deleteTargetIsBusy) return
    setDeleting(true)
    setDeleteError(null)
    try {
      let attachmentCleanupPending = false
      if (deleteTarget.kind === 'project') {
        const outcome = await api.deleteProject(deleteTarget.id)
        attachmentCleanupPending = outcome.attachmentCleanupPending
        deletedProjectIdsRef.current.add(deleteTarget.id)
        const remaining = projects.filter((item) => item.id !== deleteTarget.id)
        setProjects((old) => old.filter((item) => item.id !== deleteTarget.id))
        if (selectedProjectIdRef.current === deleteTarget.id) {
          // 先切换同步 ref，迟到的发送、列表与轮询响应便不能复活被删会话。
          const nextProjectId = remaining[0]?.id ?? null
          selectedProjectIdRef.current = nextProjectId
          chooseSession(null)
          setSessions([])
          setSelectedProjectId(nextProjectId)
          if (nextProjectId) localStorage.setItem(PROJECT_KEY, nextProjectId)
          else localStorage.removeItem(PROJECT_KEY)
          setPanelError(null)
        }
      } else {
        const outcome = await api.deleteSession(deleteTarget.id)
        attachmentCleanupPending = outcome.attachmentCleanupPending
        deletedSessionIdsRef.current.add(deleteTarget.id)
        const remaining = sessions.filter((item) => item.id !== deleteTarget.id)
        setSessions((old) => old.filter((item) => item.id !== deleteTarget.id))
        if (selectedSessionIdRef.current === deleteTarget.id) {
          chooseSession(remaining[0]?.id ?? null)
          setPanelError(null)
        }
      }
      if (attachmentCleanupPending) {
        showCleanupNotice('记录已删除，附件文件清理待重试；请检查附件目录权限。')
      }
      setDeleteTarget(null)
    } catch (error) {
      setDeleteError(errorMessage(error))
    } finally {
      setDeleting(false)
    }
  }

  async function submitModal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!modal) return
    const name = modal.mode === 'create' && modal.kind === 'project'
      ? (projectNameFromFolder(formWorkspacePath) ?? '') : formName.trim()
    if (modal.mode === 'rename') {
      if (!name) { setFormError('请输入名称。'); return }
      if (name === modal.originalName) { setModal(null); return }
    } else if (modal.kind === 'project') {
      if (!formWorkspacePath.trim()) { setFormError('请输入工作文件夹的绝对路径。'); return }
      if (!name || name.length > 100) {
        setFormError('文件夹名称无法用作项目名，请选择名称不超过 100 字的普通文件夹。')
        return
      }
    }
    setSaving(true)
    setFormError(null)
    try {
      if (modal.mode === 'rename' && modal.kind === 'project') {
        const renamed = await api.renameProject(modal.id, name)
        setProjects((old) => old.map((item) => item.id === renamed.id ? renamed : item))
      } else if (modal.mode === 'rename' && modal.kind === 'session') {
        // 重命名只变更标题；在途 Run、会话所选内核和模型均保持原绑定。
        const renamed = await api.renameSession(modal.id, name)
        setSessions((old) => old.map((item) => item.id === renamed.id ? renamed : item))
        setDetail((old) => old?.session.id === renamed.id ? { ...old, session: renamed } : old)
      } else if (modal.kind === 'project') {
        activateCreatedProject(await api.createProject(name, formWorkspacePath))
      } else if (selectedProjectId) {
        if (!formKernel) { setFormError('请选择主智能体内核。'); return }
        if (modelOptions.length && !selectedFormModel) { setFormError('请选择模型。'); return }
        // 旧本机服务没有模型目录，也不接受 model 字段；保持原请求形状。
        const modelId = bootstrap?.models ? selectedFormModel?.id : undefined
        const next = await api.createSession(selectedProjectId, formKernel, modelId)
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
    const projectId = session?.project_id ?? selectedProjectId
    if (!selectedSessionId || !modelStatus?.configured || !text || sending || sessionBusy) return
    const images = selectedImagesRef.current
    if (images.length && (!modelStatus.image_input || !projectId)) return
    const sessionId = selectedSessionId
    let acceptedRunId: string | null = null
    const uploadedImageIds: string[] = []
    setSending(true)
    setPanelError(null)
    try {
      // 按预览顺序逐张上传；轮次 JSON 只带附件 ID，不携带图片字节或 base64。
      if (projectId) {
        for (const image of images) {
          if (selectedSessionIdRef.current !== sessionId) return
          uploadedImageIds.push((await api.uploadImage(projectId, image.file)).id)
        }
      }
      if (selectedSessionIdRef.current !== sessionId) return
      const { run_id } = await api.sendTurn(sessionId, text, uploadedImageIds)
      acceptedRunId = run_id
      if (selectedSessionIdRef.current !== sessionId) return
      setPendingRunId(run_id)
      setDraft('')
      updateSelectedImages([])
      const next = await api.session(sessionId)
      if (!next.runs.some((run) => run.id === run_id)) next.runs.push(await api.run(run_id))
      if (selectedSessionIdRef.current !== sessionId) return
      setDetail(next)
      setSelectedRunId(run_id)
      setPendingRunId(null)
    } catch (error) {
      if (selectedSessionIdRef.current === sessionId) setPanelError(acceptedRunId ? `本轮已受理（运行 ID：${acceptedRunId}），但记录加载失败。请刷新会话查看，勿重复发送。` : errorMessage(error))
    } finally {
      // 任何一步失败或切走会话，都逐张清理已经上传而未绑定 Run 的草稿。
      if (!acceptedRunId && projectId && uploadedImageIds.length) {
        let cleanupIncomplete = false
        for (const attachmentId of uploadedImageIds) {
          try {
            const outcome = await api.discardImage(projectId, attachmentId)
            if (outcome.attachmentCleanupPending) cleanupIncomplete = true
          } catch {
            cleanupIncomplete = true
          }
        }
        if (cleanupIncomplete) showCleanupNotice('部分图片草稿清理失败或结果无法确认，附件文件可能仍留在本机；请检查附件目录权限。')
      }
      setSending(false)
    }
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  function addImages(files: File[]) {
    if (!files.length) return
    if (!modelStatus?.image_input) {
      setPanelError('当前会话模型未启用图片输入。请在模型设置确认支持后开启，或新建会话选择支持图片的模型。')
      return
    }
    const next = [...selectedImagesRef.current]
    const previousCount = next.length
    let unsupported = 0
    let invalidSize = 0
    let overLimit = 0
    for (const file of files) {
      if (!IMAGE_TYPES.has(file.type)) {
        unsupported += 1
        continue
      }
      if (file.size === 0 || file.size > MAX_IMAGE_BYTES) {
        invalidSize += 1
        continue
      }
      if (next.length >= MAX_IMAGES_PER_TURN) {
        overLimit += 1
        continue
      }
      next.push({ id: ++nextImageIdRef.current, file, previewUrl: URL.createObjectURL(file) })
    }
    const added = next.length - previousCount
    if (added) updateSelectedImages(next)
    const problems = [
      unsupported && `${unsupported} 个文件不是 PNG/JPEG 图片`,
      invalidSize && `${invalidSize} 张图片为空或超过 5 MiB`,
      overLimit && `${overLimit} 张图片超出每轮最多 ${MAX_IMAGES_PER_TURN} 张的限制`,
    ].filter(Boolean)
    setPanelError(problems.length ? `${added ? `已添加 ${added} 张图片；` : '未添加图片：'}${problems.join('；')}。` : null)
  }

  function handleComposerPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = event.clipboardData.files.length
      ? Array.from(event.clipboardData.files)
      : Array.from(event.clipboardData.items).filter((item) => item.kind === 'file')
        .map((item) => item.getAsFile()).filter((file): file is File => file !== null)
    if (!files.length) return
    // 既有文字又有图片的剪贴板需要自己写回文字，避免 preventDefault 吞掉文字。
    event.preventDefault()
    const target = event.currentTarget
    const pastedText = event.clipboardData.getData('text/plain')
    if (pastedText) {
      const start = target.selectionStart
      const end = target.selectionEnd
      const inserted = pastedText.slice(0, Math.max(0, 20000 - target.value.length + end - start))
      setDraft(`${target.value.slice(0, start)}${inserted}${target.value.slice(end)}`)
      requestAnimationFrame(() => target.setSelectionRange(start + inserted.length, start + inserted.length))
    }
    addImages(files)
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
              <div className="sidebar-entry-wrapper" onContextMenu={(event) => { event.preventDefault(); setActionMenu({ kind: 'project', id: item.id }) }}>
                <div className={`sidebar-entry project-entry ${item.id === selectedProjectId ? 'active' : ''}`}>
                  <button className="project-item" onClick={() => chooseProject(item.id)} title={item.workspace_path ? `${item.name} · ${item.workspace_path}` : item.name}>
                    {item.id === selectedProjectId ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <FolderClosed size={16} />
                    <span>{item.name}</span>
                  </button>
                  {item.id === selectedProjectId && <button type="button" className="sidebar-action" onClick={() => openModal('session')} aria-label={`在项目「${item.name}」中新建会话`} title="新建会话"><Plus size={15} strokeWidth={1.8} /></button>}
                  <button type="button" className="sidebar-action" data-sidebar-actions onClick={() => toggleActionMenu('project', item.id)} aria-label={`项目「${item.name}」的更多操作`} aria-expanded={actionMenu?.kind === 'project' && actionMenu.id === item.id} aria-haspopup="menu" title="更多操作"><MoreHorizontal size={17} strokeWidth={1.8} /></button>
                </div>
                {actionMenu?.kind === 'project' && actionMenu.id === item.id && <div className="sidebar-menu" data-sidebar-actions role="menu" aria-label={`项目「${item.name}」的操作`}>
                  <button type="button" role="menuitem" onClick={() => openRenameModal('project', item.id, item.name)}><Pencil size={14} /> 重命名</button>
                  <button type="button" role="menuitem" className="menu-danger" onClick={() => openDeleteModal('project', item.id, item.name)}><Trash2 size={14} /> 删除项目</button>
                </div>}
              </div>
              {item.id === selectedProjectId && (
                <div className="session-group">
                  {loadingSessions && <div className="sidebar-loading"><LoaderCircle className="spin" size={14} /> 加载会话…</div>}
                  {!loadingSessions && sessions.length === 0 && <div className="sidebar-empty session-empty">这个项目还没有会话。</div>}
                  {sessions.map((item) => (
                    <div key={item.id} className="sidebar-entry-wrapper" onContextMenu={(event) => { event.preventDefault(); setActionMenu({ kind: 'session', id: item.id }) }}>
                      <div className={`sidebar-entry session-entry ${item.id === selectedSessionId ? 'active' : ''}`}>
                        <button className="session-item" onClick={() => { setActionMenu(null); chooseSession(item.id); setSidebarOpen(false) }} title={item.title}>
                          <MessageSquareText size={15} /><span>{item.title}</span>
                        </button>
                        <button type="button" className="sidebar-action" data-sidebar-actions onClick={() => toggleActionMenu('session', item.id)} aria-label={`会话「${item.title}」的更多操作`} aria-expanded={actionMenu?.kind === 'session' && actionMenu.id === item.id} aria-haspopup="menu" title="更多操作"><MoreHorizontal size={17} strokeWidth={1.8} /></button>
                      </div>
                      {actionMenu?.kind === 'session' && actionMenu.id === item.id && <div className="sidebar-menu" data-sidebar-actions role="menu" aria-label={`会话「${item.title}」的操作`}>
                        <button type="button" role="menuitem" onClick={() => openRenameModal('session', item.id, item.title)}><Pencil size={14} /> 重命名</button>
                        <button type="button" role="menuitem" className="menu-danger" onClick={() => openDeleteModal('session', item.id, item.title)}><Trash2 size={14} /> 删除会话</button>
                      </div>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="sidebar-footer">
          <button type="button" onClick={() => { setShowModelSettings(true); setSidebarOpen(false) }}><Settings2 size={16} strokeWidth={1.7} /> 模型设置</button>
        </div>
      </aside>

      <main className="main-pane">
        <header className="topbar">
          <div className="topbar-leading">
            <button className="icon-button mobile-nav-button" title="项目导航" aria-label="打开项目导航" onClick={() => setSidebarOpen(true)}><Menu size={20} /></button>
            <div className="title-stack"><span title={project?.workspace_path ?? undefined}>{project?.workspace_path ? `${project.name} · ${project.workspace_path}` : (project?.name ?? '工作空间')}</span><h1>{session?.title ?? (project ? '选择或新建会话' : '欢迎使用玄月')}</h1></div>
          </div>
        </header>

        {selectedSessionId && <div className="view-bar">
          <div className="view-tabs" role="group" aria-label="会话视图">
            <button type="button" aria-pressed={activeView === 'chat'} className={activeView === 'chat' ? 'active' : ''} onClick={() => setActiveView('chat')}>对话</button>
            <button type="button" aria-pressed={activeView === 'trace'} className={activeView === 'trace' ? 'active' : ''} onClick={() => setActiveView('trace')}>执行轨迹</button>
          </div>
          <span className="session-kernel">
            主智能体：{session?.kernel ?? '读取中'}
            {session?.model ? ` · 模型：${session.model}` : ''}
          </span>
        </div>}

        {cleanupNotice && <div className="error-banner" role="status"><span>{cleanupNotice}</span><button className="icon-button" onClick={() => { setCleanupNotice(null); localStorage.removeItem(CLEANUP_NOTICE_KEY) }} aria-label="关闭附件清理提示"><X size={16} /></button></div>}
        {panelError && <div className="error-banner" role="alert"><span>{panelError}</span><button className="icon-button" onClick={() => setPanelError(null)} aria-label="关闭错误"><X size={16} /></button></div>}

        {!project ? (
          <div className="main-empty"><span className="main-empty-icon"><FolderClosed size={21} strokeWidth={1.7} /></span><h1>从一个项目开始</h1><p>项目用于归拢会话；每个会话可选择自己的主智能体内核和模型。</p><button className="primary-button" onClick={() => openModal('project')}><Plus size={16} /> 新建项目</button></div>
        ) : !selectedSessionId ? (
          <div className="main-empty"><span className="main-empty-icon"><MessageSquareText size={21} strokeWidth={1.7} /></span><h1>在「{project.name}」里开始对话</h1><p>新建会话时选择主智能体内核和模型，之后就可以连续提问。</p><button className="primary-button" onClick={() => openModal('session')}><Plus size={16} /> 新建会话</button></div>
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
                updateVisibleRun(element)
              }} className={`conversation-scroll ${!loadingDetail && sortedRuns.length === 0 ? 'empty-conversation' : ''}`}>
                <div className="conversation-content">
                  {loadingDetail && !detail ? <div className="content-loading"><LoaderCircle className="spin" size={18} /> 加载会话记录…</div> : null}
                  {!loadingDetail && sortedRuns.length === 0 && <div className="conversation-welcome">
                    <div className="welcome-symbol"><Sparkles size={21} strokeWidth={1.7} /></div>
                    <span className="welcome-kicker">新的会话</span>
                    <h2>从一个问题开始</h2>
                    <p>描述你想了解的事，玄月会在当前会话中保留回复和公开执行轨迹。</p>
                  </div>}
                  {sortedRuns.map((run) => <div key={run.id} data-turn-id={run.id} className="conversation-turn"><RunCard run={run} projectId={session?.project_id ?? ''} onSelect={() => { setSelectedRunId(run.id); setActiveView('trace') }} /></div>)}
                </div>
              </div>
              <TurnNavigator runs={sortedRuns} activeRunId={visibleRunId ?? sortedRuns.at(-1)?.id ?? null} onNavigate={navigateToRun} />
              <div className="composer-area">
                {!modelStatus?.configured && <div className="composer-warning">
                  <span>{!modelStatus ? loadingDetail ? '正在读取会话模型状态…' : '未能读取会话模型状态，请刷新并检查本机服务。' : modelStatus.id ? `本会话绑定的 ${modelStatus.id} 模型不可用，请在本机恢复其配置。` : '模型尚未配置。请配置后再发送消息。'}</span>
                  <button type="button" className="inline-link" onClick={() => setShowModelSettings(true)}>打开模型设置</button>
                </div>}
                {sessionBusy && <div className="composer-warning">{pendingRunId ? '本轮已提交，正在同步运行记录；请勿重复发送。' : '本会话正在运行，请等待当前回复。'}</div>}
                <form className="composer" onSubmit={sendTurn}>
                  {selectedImages.length > 0 && <div className="composer-attachments" role="group" aria-label={`待发送图片 ${selectedImages.length} 张`}>
                    {selectedImages.map((image, index) => <div className="composer-attachment" key={image.id}>
                      <img src={image.previewUrl} alt="" />
                      <span title={image.file.name}>{image.file.name || `图片 ${index + 1}`}</span>
                      <button type="button" className="icon-button" aria-label={`移除第 ${index + 1} 张图片：${image.file.name || '未命名图片'}`} title="移除图片" onClick={() => updateSelectedImages(selectedImagesRef.current.filter((item) => item.id !== image.id))} disabled={sending}><X size={15} /></button>
                    </div>)}
                  </div>}
                  <textarea ref={composerInputRef} aria-label="输入消息" placeholder="向玄月提问…" rows={2} maxLength={20000} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={handleComposerKey} onPaste={handleComposerPaste} disabled={!modelStatus?.configured || sending || sessionBusy} />
                  {selectedImages.length > 0 && !draft.trim() && <span className="composer-hint">请先输入问题，再发送图片。</span>}
                  <div className="composer-bottom">
                    <div className="composer-actions">
                      <input ref={imageInputRef} className="attachment-input" type="file" accept="image/png,image/jpeg" multiple tabIndex={-1} onChange={(event) => { addImages(Array.from(event.target.files ?? [])); event.target.value = ''; composerInputRef.current?.focus() }} />
                      <button type="button" className="icon-button attachment-button" aria-label="添加图片" title={modelStatus?.image_input ? `添加 PNG/JPEG 图片（最多 ${MAX_IMAGES_PER_TURN} 张）` : '当前模型未启用图片输入'} onClick={() => imageInputRef.current?.click()} disabled={!modelStatus?.configured || !modelStatus.image_input || selectedImages.length >= MAX_IMAGES_PER_TURN || sending || sessionBusy}><ImagePlus size={18} /></button>
                      <span>{selectedImages.length ? `图片 ${selectedImages.length}/${MAX_IMAGES_PER_TURN} · ` : ''}Enter 发送 · Shift + Enter 换行</span>
                    </div>
                    <button className="send-button" title="发送消息" aria-label="发送消息" type="submit" disabled={!draft.trim() || !modelStatus?.configured || sending || sessionBusy}>{sending ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}</button>
                  </div>
                </form>
              </div>
            </>}
          </>
        )}
      </main>

      {modal && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving && !selectingFolder) setModal(null) }}>
        <form className="modal-card" onSubmit={submitModal}>
          <div className="modal-top"><span className="modal-icon">{modal.kind === 'project' ? <FolderClosed size={18} strokeWidth={1.7} /> : <MessageSquareText size={18} strokeWidth={1.7} />}</span><button type="button" className="icon-button" onClick={() => setModal(null)} aria-label="关闭" disabled={saving || selectingFolder}><X size={18} /></button></div>
          <h2>{modal.mode === 'rename' ? '重命名' : '新建'}{modal.kind === 'project' ? '项目' : '会话'}</h2>
          {modal.mode === 'create' && <p>{modal.kind === 'project' ? '选择一个本机文件夹，项目会使用它的名称。' : `在「${project?.name ?? ''}」中开始对话；首次提问后自动生成会话标题。`}</p>}
          {modal.mode === 'rename' && <>
            <label htmlFor="new-name">{modal.kind === 'project' ? '项目名称' : '会话名称'}</label>
            <input id="new-name" autoFocus maxLength={modal.kind === 'project' ? 100 : 200} value={formName} onChange={(event) => setFormName(event.target.value)} />
          </>}
          {modal.mode === 'create' && modal.kind === 'project' && <>
            <label htmlFor="workspace-path">工作文件夹</label>
            <input id="workspace-path" autoFocus value={formWorkspacePath} onChange={(event) => setFormWorkspacePath(event.target.value)} placeholder="输入本机文件夹的绝对路径" spellCheck={false} />
            <small className="field-help">项目名将使用文件夹名称{projectNameFromFolder(formWorkspacePath) ? `「${projectNameFromFolder(formWorkspacePath)}」` : ''}。网页预览无法调用系统目录选择器。</small>
          </>}
          {modal.mode === 'create' && modal.kind === 'session' && <>
            <label htmlFor="new-kernel">主智能体内核</label>
            <SelectionPopover id="new-kernel" ariaLabel="选择主智能体内核" value={formKernel} onChange={setFormKernel} options={bootstrap.kernels.map((kernel) => ({ value: kernel, label: kernel }))} />
            {modelOptions.length ? (
              <>
                <label htmlFor="new-model">模型</label>
                <SelectionPopover id="new-model" ariaLabel="选择模型" value={formModel} onChange={setFormModel} options={modelOptions.map((model) => ({
                  value: model.id,
                  label: model.id,
                  description: `${model.id === bootstrap.model.id ? '默认 · ' : ''}${model.configured ? '已配置' : '暂不可用'}${model.destination ? ` · ${model.destination}` : ''}`,
                }))} />
                <small className="field-help">
                  {selectedFormModel?.configured ? '已配置，可尝试调用' : '配置未就绪'}
                  {selectedFormModel?.destination ? ` · 目标域名 ${selectedFormModel.destination}` : ''}
                  。会话创建后保留所选模型。
                </small>
                <button type="button" className="field-link" onClick={() => { setModal(null); setShowModelSettings(true) }}>管理模型配置</button>
              </>
            ) : (
              <div className="field-help">尚未登记模型。<button type="button" className="inline-link" onClick={() => { setModal(null); setShowModelSettings(true) }}>打开模型设置</button></div>
            )}
          </>}
          {formError && <div className="form-error">{formError}</div>}
          <div className="modal-actions"><button className="secondary-button" type="button" onClick={() => setModal(null)} disabled={saving || selectingFolder}>取消</button><button className="primary-button" type="submit" disabled={saving || selectingFolder}>{saving ? <LoaderCircle className="spin" size={16} /> : modal.mode === 'rename' ? <Pencil size={15} /> : <Plus size={16} />} {modal.mode === 'rename' ? '保存' : '创建'}</button></div>
        </form>
      </div>}

      {deleteTarget && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !deleting) setDeleteTarget(null) }}>
        <form className="modal-card" role="dialog" aria-modal="true" aria-labelledby="delete-title" aria-describedby="delete-description" onSubmit={confirmDelete}>
          <div className="modal-top"><span className="modal-icon delete-icon"><Trash2 size={18} strokeWidth={1.7} /></span><button type="button" className="icon-button" onClick={() => setDeleteTarget(null)} aria-label="关闭" disabled={deleting}><X size={18} /></button></div>
          <h2 id="delete-title">删除{deleteTarget.kind === 'project' ? '项目' : '会话'}？</h2>
          <p id="delete-description" className="delete-description">
            {deleteTarget.kind === 'project'
              ? <>确定删除项目「<strong>{deleteTarget.name}</strong>」吗？项目下的所有会话、运行记录和附件也会删除，无法撤销。</>
              : <>确定删除会话「<strong>{deleteTarget.name}</strong>」吗？该会话的运行记录和附件也会删除，无法撤销。</>}
          </p>
          {deleteTargetIsBusy && <div className="form-error">当前会话正在发送或运行，请等待任务结束后再删除。</div>}
          {deleteError && <div className="form-error" role="alert">{deleteError}</div>}
          <div className="modal-actions"><button className="secondary-button" type="button" autoFocus onClick={() => setDeleteTarget(null)} disabled={deleting}>取消</button><button className="danger-button" type="submit" disabled={deleting || deleteTargetIsBusy}>{deleting ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={15} />} 删除{deleteTarget.kind === 'project' ? '项目' : '会话'}</button></div>
        </form>
      </div>}
      {showModelSettings && <ModelSettings onClose={() => setShowModelSettings(false)} onSaved={refreshModelCatalog} />}
    </div>
  )
}
