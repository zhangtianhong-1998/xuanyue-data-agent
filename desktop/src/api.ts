import type { Bootstrap, Project, Run, Session, SessionDetail } from './types'

// 所有数据都来自本机产品 API；统一处理非 2xx 和不可解析的响应，避免 UI 静默失败。
async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      Accept: 'application/json',
      'X-Xuanyue-Client': 'desktop-dev',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

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

const segment = (id: string) => encodeURIComponent(id)

export const api = {
  bootstrap: () => request<Bootstrap>('/bootstrap'),
  createProject: (name: string) => request<Project>('/projects', { name }),
  listSessions: (projectId: string) =>
    request<{ sessions: Session[] }>(`/projects/${segment(projectId)}/sessions`),
  createSession: (projectId: string, title: string, kernel: string) =>
    request<Session>(`/projects/${segment(projectId)}/sessions`, { title, kernel }),
  session: (sessionId: string) => request<SessionDetail>(`/sessions/${segment(sessionId)}`),
  sendTurn: (sessionId: string, text: string) =>
    request<{ run_id: string }>(`/sessions/${segment(sessionId)}/turns`, { text }),
  run: (runId: string) => request<Run>(`/runs/${segment(runId)}`),
}
