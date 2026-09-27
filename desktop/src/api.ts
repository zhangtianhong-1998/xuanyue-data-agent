import type { Bootstrap, ImageAttachment, Project, Run, Session, SessionDetail } from './types'

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

const segment = (id: string) => encodeURIComponent(id)

export const api = {
  bootstrap: () => request<Bootstrap>('/bootstrap'),
  createProject: (name: string) => request<Project>('/projects', { name }),
  renameProject: (projectId: string, name: string) =>
    request<Project>(`/projects/${segment(projectId)}`, { name }, 'PATCH'),
  listSessions: (projectId: string) =>
    request<{ sessions: Session[] }>(`/projects/${segment(projectId)}/sessions`),
  createSession: (projectId: string, title: string, kernel: string, model?: string) =>
    request<Session>(`/projects/${segment(projectId)}/sessions`, { title, kernel, ...(model ? { model } : {}) }),
  renameSession: (sessionId: string, title: string) =>
    request<Session>(`/sessions/${segment(sessionId)}`, { title }, 'PATCH'),
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
  discardImage: async (projectId: string, attachmentId: string) => {
    const response = await fetch(`/api/projects/${segment(projectId)}/attachments/${segment(attachmentId)}`, {
      method: 'DELETE',
      headers: { 'X-Xuanyue-Client': 'desktop-dev' },
    })
    if (!response.ok) throw new Error(`图片草稿清理失败（HTTP ${response.status}）`)
  },
  attachmentUrl: (projectId: string, attachmentId: string) =>
    `/api/projects/${segment(projectId)}/attachments/${segment(attachmentId)}`,
  sendTurn: (sessionId: string, text: string, attachmentIds: string[] = []) =>
    request<{ run_id: string }>(`/sessions/${segment(sessionId)}/turns`, {
      text,
      ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}),
    }),
  run: (runId: string) => request<Run>(`/runs/${segment(runId)}`),
}
