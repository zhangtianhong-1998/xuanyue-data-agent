import type { Bootstrap, ImageAttachment, ModelConfig, ModelConfigUpdate, Project, Run, Session, SessionDetail } from './types'

// 产品 API 的报错可能来自模型供应商，界面只展示本机可行动的提示。
async function readResponse<T>(response: Response): Promise<T> {
  const data: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    // 服务端错误可能来自模型供应商；不把未经审查的原始错误文案呈现给用户。
    if (response.status === 409) throw new Error('本会话正在运行，请等待当前回复。')
    if (response.status === 503) throw new Error('本会话绑定的模型不可用，请检查本机配置。')
    throw new Error(`请求失败（HTTP ${response.status}），请检查本机服务与配置。`)
  }
  if (data === null) throw new Error('服务返回了空响应')
  return data as T
}

// 所有文本数据都来自本机产品 API；图片上传单独传原始字节。
async function request<T>(path: string, body?: unknown, method?: 'POST' | 'PATCH'): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    headers: {
      Accept: 'application/json',
      'X-Xuanyue-Client': 'desktop-dev',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return readResponse<T>(response)
}

export interface DeleteOutcome {
  attachmentCleanupPending: boolean
}

// 204 表示记录与附件均清理完；202 表示记录已删，但本机附件文件待重试。
async function remove(path: string, kind: '项目' | '会话' | '图片草稿'): Promise<DeleteOutcome> {
  const response = await fetch(`/api${path}`, {
    method: 'DELETE',
    headers: { 'X-Xuanyue-Client': 'desktop-dev' },
  })
  if (response.status === 204) return { attachmentCleanupPending: false }
  if (response.status === 202) {
    const data: unknown = await response.json().catch(() => null)
    if (data && typeof data === 'object' && 'status' in data && data.status === 'deleted'
      && 'attachment_cleanup' in data && data.attachment_cleanup === 'pending') {
      return { attachmentCleanupPending: true }
    }
    throw new Error(`删除${kind}的结果无法确认，请刷新页面核对。`)
  }
  if (response.status === 409) throw new Error(kind === '图片草稿'
    ? '图片草稿正在使用，暂不能清理。'
    : `${kind}仍有任务正在运行，请等待任务结束后再删除。`)
  if (response.status === 404) throw new Error(`${kind}已不存在，请刷新页面。`)
  throw new Error(`删除${kind}失败（HTTP ${response.status}），请检查本机服务。`)
}

const segment = (id: string) => encodeURIComponent(id)

export const api = {
  bootstrap: () => request<Bootstrap>('/bootstrap'),
  modelConfig: async () => {
    const response = await fetch('/api/model-config', {
      headers: { Accept: 'application/json', 'X-Xuanyue-Client': 'desktop-dev' },
    })
    if (response.status === 409) throw new Error('本机模型配置文件无效，请先修复后再打开模型设置。')
    return readResponse<ModelConfig>(response)
  },
  saveModelConfig: async (config: ModelConfigUpdate) => {
    const response = await fetch('/api/model-config', {
      method: 'PUT',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Xuanyue-Client': 'desktop-dev',
      },
      body: JSON.stringify(config),
    })
    // 配置报错与“会话正在运行”的 409 含义不同，不展示供应商原始文案。
    if (response.status === 400) throw new Error('模型配置无效，请检查 ID、地址和模型关联。')
    if (response.status === 409) {
      const data: unknown = await response.json().catch(() => null)
      if (data && typeof data === 'object' && 'error' in data && data.error === 'model_config_invalid') {
        throw new Error('本机模型配置文件无效。请先手动修复，界面不会覆盖它。')
      }
      throw new Error('已有会话使用被删除的模型。请保留该模型，或先删除相关会话。')
    }
    return readResponse<ModelConfig>(response)
  },
  createProject: (name: string, workspacePath: string) =>
    request<Project>('/projects', { name, workspace_path: workspacePath }),
  renameProject: (projectId: string, name: string) =>
    request<Project>(`/projects/${segment(projectId)}`, { name }, 'PATCH'),
  deleteProject: (projectId: string) => remove(`/projects/${segment(projectId)}`, '项目'),
  listSessions: (projectId: string) =>
    request<{ sessions: Session[] }>(`/projects/${segment(projectId)}/sessions`),
  createSession: (projectId: string, kernel: string, model?: string) =>
    // 省略 title 表示由服务在首轮提问后生成；显式 title 用于手工创建的旧客户端。
    request<Session>(`/projects/${segment(projectId)}/sessions`, { kernel, ...(model ? { model } : {}) }),
  renameSession: (sessionId: string, title: string) =>
    request<Session>(`/sessions/${segment(sessionId)}`, { title }, 'PATCH'),
  deleteSession: (sessionId: string) => remove(`/sessions/${segment(sessionId)}`, '会话'),
  session: (sessionId: string) => request<SessionDetail>(`/sessions/${segment(sessionId)}`),
  uploadImage: async (projectId: string, file: File) => {
    const response = await fetch(`/api/projects/${segment(projectId)}/attachments`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'X-Xuanyue-Client': 'desktop-dev',
        'Content-Type': file.type,
      },
      body: file,
    })
    return readResponse<ImageAttachment>(response)
  },
  discardImage: (projectId: string, attachmentId: string) =>
    remove(`/projects/${segment(projectId)}/attachments/${segment(attachmentId)}`, '图片草稿'),
  attachmentUrl: (projectId: string, attachmentId: string) =>
    `/api/projects/${segment(projectId)}/attachments/${segment(attachmentId)}`,
  sendTurn: (sessionId: string, text: string, attachmentIds: string[] = []) =>
    request<{ run_id: string }>(`/sessions/${segment(sessionId)}/turns`, {
      text,
      ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}),
    }),
  run: (runId: string) => request<Run>(`/runs/${segment(runId)}`),
}
