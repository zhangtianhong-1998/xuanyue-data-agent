import type { RunEvent } from '../types.ts'

export interface ToolActivity {
  id: string
  callId: string
  parentId: string
  seq: number
  name: string
  input: string
  output: string
  callFinished: boolean
  resultFinished: boolean
  resultState: string | null
}

export interface ModelActivity {
  id: string
  seq: number
  model: string | null
  publicText: string
  finished: boolean
  tools: ToolActivity[]
}

export interface UnlinkedActivity {
  seq: number
  kind: string
  reason: string
  detail: string | null
}

export interface ActivitySummary {
  models: ModelActivity[]
  toolCount: number
  coverageGaps: number
  unlinked: UnlinkedActivity[]
}

const MODEL_EVENTS = new Set(['model_call_started', 'model_call_finished', 'text_delta'])
const TOOL_EVENTS = new Set([
  'tool_call_started', 'tool_call_delta', 'tool_call_finished',
  'tool_result_started', 'tool_result_delta', 'tool_result_finished',
])

function field(event: RunEvent, name: string): string {
  const value = event.payload[name]
  return typeof value === 'string' ? value : ''
}

function idField(event: RunEvent, name: string): string {
  return field(event, name).trim()
}

function publicDetail(event: RunEvent): string | null {
  // 这些字段已经过服务端公开事件投影，旧记录在未关联列表中仍可逐条查看。
  let key = ''
  if (event.kind.endsWith('_delta')) key = 'delta'
  else if (event.kind === 'model_call_started') key = 'model_name'
  else if (event.kind === 'model_call_finished') key = 'finished_reason'
  else if (event.kind === 'tool_call_started' || event.kind === 'tool_result_started') key = 'tool_call_name'
  else if (event.kind === 'tool_result_finished') key = 'state'
  const callId = field(event, 'tool_call_id')
  const parts = [callId ? `调用 ID：${callId}` : '', key ? field(event, key) : ''].filter(Boolean)
  return parts.length ? parts.join('\n') : null
}

/** Assemble only activities with explicit public IDs; legacy events never acquire guessed parents. */
export function summarizeActivity(events: readonly RunEvent[]): ActivitySummary {
  const ordered = [...events].sort((a, b) => a.seq - b.seq)
  const models = new Map<string, ModelActivity>()
  const tools = new Map<string, ToolActivity>()
  const modelStarts = new Map<string, RunEvent>()
  const toolStarts = new Map<string, RunEvent>()
  const invalidModels = new Set<string>()
  const invalidTools = new Set<string>()
  const unlinked: UnlinkedActivity[] = []
  const unlinkedSeq = new Set<number>()
  let coverageGaps = 0
  const missing = (event: RunEvent, reason: string) => {
    if (unlinkedSeq.has(event.seq)) return
    unlinkedSeq.add(event.seq)
    unlinked.push({ seq: event.seq, kind: event.kind, reason, detail: publicDetail(event) })
  }

  // 先确认所有开始事件；重复 ID 整组失效，后续事件不能误挂到首次使用者。
  for (const event of ordered) {
    if (event.kind !== 'model_call_started') continue
    const id = idField(event, 'activity_id')
    if (!id) missing(event, '旧事件缺少 activity_id')
    else if (invalidModels.has(id)) missing(event, '模型活动 ID 重复，无法关联')
    else if (models.has(id)) {
      invalidModels.add(id)
      const first = modelStarts.get(id)
      if (first) missing(first, '模型活动 ID 重复，无法关联')
      missing(event, '模型活动 ID 重复，无法关联')
      models.delete(id)
    } else {
      modelStarts.set(id, event)
      models.set(id, {
        id, seq: event.seq, model: field(event, 'model_name') || null,
        publicText: '', finished: false, tools: [],
      })
    }
  }

  for (const event of ordered) {
    if (event.kind !== 'tool_call_started') continue
    const id = idField(event, 'activity_id')
    const parentId = idField(event, 'parent_activity_id')
    const callId = idField(event, 'tool_call_id')
    const parent = models.get(parentId)
    if (!id) missing(event, '旧事件缺少 activity_id')
    else if (invalidTools.has(id)) missing(event, '工具活动 ID 重复，无法关联')
    else if (toolStarts.has(id)) {
      invalidTools.add(id)
      const first = toolStarts.get(id)
      if (first) missing(first, '工具活动 ID 重复，无法关联')
      missing(event, '工具活动 ID 重复，无法关联')
      const previous = tools.get(id)
      if (previous) {
        const oldParent = models.get(previous.parentId)
        if (oldParent) oldParent.tools = oldParent.tools.filter((item) => item.id !== id)
        tools.delete(id)
      }
    } else if (!parentId || !callId) {
      toolStarts.set(id, event)
      missing(event, '工具开始事件缺少父活动或调用 ID')
    } else if (!parent || event.seq < parent.seq) {
      toolStarts.set(id, event)
      missing(event, '工具开始事件找不到已开始的父模型调用')
    } else {
      toolStarts.set(id, event)
      const tool: ToolActivity = {
        id, callId, parentId, seq: event.seq, name: field(event, 'tool_call_name'),
        input: '', output: '', callFinished: false, resultFinished: false,
        resultState: null,
      }
      tools.set(id, tool)
      parent.tools.push(tool)
    }
  }

  for (const event of ordered) {
    if (event.kind === 'coverage_gap') {
      coverageGaps += 1
      continue
    }
    if (MODEL_EVENTS.has(event.kind)) {
      if (event.kind === 'model_call_started') continue
      const id = idField(event, 'activity_id')
      const model = models.get(id)
      if (!id) missing(event, '旧事件缺少 activity_id')
      else if (invalidModels.has(id)) missing(event, '模型活动 ID 重复，无法关联')
      else if (!model || event.seq < model.seq) missing(event, '找不到已开始的模型调用')
      else if (event.kind === 'model_call_finished') {
        if (model.finished) missing(event, '模型结束事件重复')
        else model.finished = true
      } else model.publicText += field(event, 'delta')
      continue
    }
    if (!TOOL_EVENTS.has(event.kind) || event.kind === 'tool_call_started') continue
    const id = idField(event, 'activity_id')
    const parentId = idField(event, 'parent_activity_id')
    const callId = idField(event, 'tool_call_id')
    const tool = tools.get(id)
    if (!id) missing(event, '旧事件缺少 activity_id')
    else if (!parentId || !callId) missing(event, '工具事件缺少父活动或调用 ID')
    else if (invalidTools.has(id)) missing(event, '工具活动 ID 重复，无法关联')
    else if (!tool || event.seq < tool.seq) missing(event, '找不到已开始的工具调用')
    else if (tool.parentId !== parentId || tool.callId !== callId) missing(event, '工具事件的父活动或调用 ID 不一致')
    else if (event.kind === 'tool_call_delta') tool.input += field(event, 'delta')
    else if (event.kind === 'tool_call_finished') {
      if (tool.callFinished) missing(event, '工具调用结束事件重复')
      else tool.callFinished = true
    } else if (event.kind === 'tool_result_started') {
      tool.name ||= field(event, 'tool_call_name')
    } else if (event.kind === 'tool_result_delta') tool.output += field(event, 'delta')
    else if (event.kind === 'tool_result_finished') {
      if (tool.resultFinished) missing(event, '工具结果结束事件重复')
      else {
        tool.resultFinished = true
        tool.resultState = field(event, 'state') || null
      }
    }
  }

  const grouped = [...models.values()].sort((a, b) => a.seq - b.seq)
  return {
    models: grouped,
    toolCount: grouped.reduce((total, model) => total + model.tools.length, 0),
    coverageGaps,
    unlinked: unlinked.sort((a, b) => a.seq - b.seq),
  }
}
