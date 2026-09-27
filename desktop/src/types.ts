/** 这些类型只描述产品 API；前端不依赖 AgentScope 或 LangGraph 的内部对象。 */
export interface User {
  id: string
  name: string
}

export interface Project {
  id: string
  name: string
  created_at: string
}

export interface Session {
  id: string
  project_id: string
  title: string
  kernel: string
  model: string | null
  created_at: string
  updated_at: string
}

export interface RunEvent {
  seq: number
  kind: string
  payload: Record<string, unknown>
  created_at: string
}

export interface Run {
  id: string
  session_id: string
  question: string
  answer: string | null
  status: string
  kernel: string
  model: string | null
  created_at: string
  updated_at: string
  error_type: string | null
  events: RunEvent[]
}

export interface SessionDetail {
  session: Session
  runs: Run[]
  model_status: ModelStatus
}

export interface ModelStatus {
  id: string | null
  configured: boolean
  destination?: string | null
}

export interface CatalogModel extends ModelStatus {
  id: string
}

export interface Bootstrap {
  user: User
  projects: Project[]
  kernels: string[]
  model: ModelStatus
  /** 旧本机服务只有 model；界面在缺少目录时仍可使用默认模型。 */
  models?: CatalogModel[]
}
