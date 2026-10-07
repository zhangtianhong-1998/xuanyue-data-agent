/** 这些类型只描述产品 API；前端不依赖 AgentScope 或 LangGraph 的内部对象。 */
export interface User {
  id: string
  name: string
}

export interface Project {
  id: string
  name: string
  /** 旧项目没有工作文件夹；选择路径本身不授予 Agent 文件读写权限。 */
  workspace_path: string | null
  created_at: string
}

/** 只在 Electron 窗口存在；浏览器预览不会注入此原生能力。 */
export interface DesktopBridge {
  chooseProjectFolder(): Promise<string | null>
}

declare global {
  interface Window {
    xuanyueDesktop?: DesktopBridge
  }
}

export interface Session {
  id: string
  project_id: string
  title: string
  kernel: string
  model: string | null
  reasoning?: string
  created_at: string
  updated_at: string
}

export interface RunEvent {
  seq: number
  kind: string
  payload: Record<string, unknown>
  created_at: string
}

/** 图片内容留在本机附件文件中；API 和事件只传不透明引用。 */
export interface ImageAttachment {
  id: string
  mime_type: string
  size: number
}

export interface Run {
  id: string
  session_id: string
  question: string
  answer: string | null
  status: string
  kernel: string
  model: string | null
  /** 当轮显示名；改名不会让旧回复显示成新型号。旧记录回退内部 ID。 */
  model_name?: string | null
  reasoning?: string
  /** 本轮开始时保存的选择；不使用模型目录的现行名称回填历史。 */
  reasoning_config?: { id?: string; label?: string; effort?: string | null; thinking?: string | null }
  created_at: string
  updated_at: string
  error_type: string | null
  events: RunEvent[]
  attachments?: ImageAttachment[]
}

export interface SessionDetail {
  session: Session
  runs: Run[]
  model_status: ModelStatus
  /** 自动命名是附加请求；主 Run 完成后标题可能仍在生成。 */
  title_state?: 'pending' | 'generating' | 'generated' | 'failed' | 'manual'
}

export interface ModelStatus {
  id: string | null
  name?: string
  provider_name?: string
  configured: boolean
  destination?: string | null
  image_input?: boolean
  provider?: string
  upstream_model?: string
  reasoning_options?: ReasoningOption[]
  default_reasoning?: string
  max_input_tokens?: number | null
  max_output_tokens?: number | null
  output_token_parameter?: 'max_tokens' | 'max_completion_tokens'
}

export interface CatalogModel extends ModelStatus {
  id: string
}

/** 模型设置只返回配置元数据；已保存的密钥永远不随 GET 返回。 */
export interface ModelProviderConfig {
  id: string
  name?: string
  protocol: 'openai_chat_completions'
  base_url: string
  key_configured: boolean
}

export interface ModelDefinitionConfig {
  id: string
  name?: string
  provider: string
  upstream_model: string
  image_input: boolean
  reasoning_options: ReasoningOption[]
  default_reasoning: string
  /** 声明的输入容量；当前不做跨供应商精确分词或自动截断。 */
  max_input_tokens?: number
  max_output_tokens?: number
  output_token_parameter?: 'max_tokens' | 'max_completion_tokens'
}

/** 一个模型可选的推理配置；原生字段只由协议客户端发出，不表示模型思考内容。 */
export interface ReasoningOption {
  id: string
  label: string
  effort?: string
  thinking?: string
}

export interface ModelConfig {
  /** 首次配置时目录可为空，此时还没有默认模型。 */
  default_model: string | null
  providers: ModelProviderConfig[]
  models: ModelDefinitionConfig[]
}

/** 保存时只提交新输入的密钥；不能把只读的 key_configured 当作密钥发回。 */
export interface ModelConfigUpdate {
  default_model: string
  providers: Array<Omit<ModelProviderConfig, 'key_configured'> & { api_key?: string }>
  models: ModelDefinitionConfig[]
}

export interface Bootstrap {
  user: User
  projects: Project[]
  kernels: string[]
  model: ModelStatus
  /** 旧本机服务只有 model；界面在缺少目录时仍可使用默认模型。 */
  models?: CatalogModel[]
}
