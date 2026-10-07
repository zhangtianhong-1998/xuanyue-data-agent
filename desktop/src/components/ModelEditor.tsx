import { ChevronRight, Plus, Trash2 } from 'lucide-react'
import type { ModelDefinitionConfig, ReasoningOption } from '../types'
import SelectionPopover from './SelectionPopover'

export const EFFORT_VALUES = ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
export const THINKING_VALUES = ['', 'enabled', 'disabled', 'auto']
export const REASONING_ID = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/

type ReasoningDraft = ReasoningOption & { localKey: string }
export type ModelDraft = Omit<ModelDefinitionConfig, 'reasoning_options'> & {
  reasoning_options: ReasoningDraft[]
  persisted: boolean
  /** 仅表单持有；切换供应商时保留未完成的 token 输入，不写入配置。 */
  capacityText?: { max_input_tokens: string; max_output_tokens: string }
}

interface ModelEditorProps {
  model: ModelDraft
  isDefault: boolean
  removable: boolean
  onChange(update: Partial<ModelDraft>): void
  onDefault(): void
  onRemove(): void
}

/** 编辑一个供应商下的模型。显示名可修改，内部 ID 保持不变，避免破坏已有会话。 */
export default function ModelEditor({ model, isDefault, removable, onChange, onDefault, onRemove }: ModelEditorProps) {
  const title = model.name?.trim() || model.upstream_model || '新模型'
  const capacityText = model.capacityText ?? {
    max_input_tokens: model.max_input_tokens?.toString() ?? '',
    max_output_tokens: model.max_output_tokens?.toString() ?? '',
  }

  function editCapacity(field: keyof typeof capacityText, value: string) {
    // 保留未完成或无效的输入，让保存校验提示用户；不能把它误当成“留空”。
    const count = value.trim() === '' ? undefined : /^\d+$/.test(value.trim()) ? Number(value) : Number.NaN
    onChange({ [field]: count, capacityText: { ...capacityText, [field]: value } })
  }

  function editReasoning(optionIndex: number, update: Partial<ReasoningOption>) {
    const previous = model.reasoning_options[optionIndex]
    onChange({
      reasoning_options: model.reasoning_options.map((option, index) => index === optionIndex ? { ...option, ...update } : option),
      default_reasoning: update.id !== undefined && model.default_reasoning === previous.id ? update.id : model.default_reasoning,
    })
  }

  function addReasoning() {
    const used = model.reasoning_options.map((option) => option.id)
    let id = 'high'
    let suffix = 1
    while (used.includes(id)) id = `high-${suffix++}`
    onChange({ reasoning_options: [...model.reasoning_options, {
      id, label: '高', effort: 'high', localKey: crypto.randomUUID(),
    }] })
  }

  function removeReasoning(index: number) {
    onChange({
      reasoning_options: model.reasoning_options.filter((_, position) => position !== index),
      default_reasoning: model.default_reasoning === model.reasoning_options[index].id ? 'default' : model.default_reasoning,
    })
  }

  return <details className="settings-model-card" open={!model.persisted || undefined}>
    <summary>
      <ChevronRight size={15} className="settings-model-chevron" />
      <span className="settings-model-title">{title}</span>
      <span className="settings-model-kind">{model.image_input ? '图文' : '文字'}</span>
      {isDefault && <span className="settings-model-default">默认</span>}
    </summary>
    <div className="settings-model-body">
      <div className="settings-fields model-fields">
        <label>模型 ID<input value={model.upstream_model} spellCheck={false} maxLength={256}
          onChange={(event) => onChange({ upstream_model: event.target.value })} placeholder="供应商提供的模型或接入点 ID" /></label>
        <label>显示名称<input value={model.name ?? ''} maxLength={128}
          onChange={(event) => onChange({ name: event.target.value })} placeholder="可选，留空使用模型 ID" /></label>
      </div>
      <div className="settings-fields model-capability-fields">
        <div className="settings-field"><span>模型类型</span><SelectionPopover ariaLabel={`模型 ${title} 的类型`}
          options={[{ value: 'text', label: '文字 LLM' }, { value: 'image', label: '图文 VLM' }]}
          value={model.image_input ? 'image' : 'text'} onChange={(value) => onChange({ image_input: value === 'image' })} /></div>
        <div className="settings-field"><span>默认推理选项</span><SelectionPopover ariaLabel={`模型 ${title} 的默认推理选项`}
          options={[{ value: 'default', label: '供应商默认' }, ...model.reasoning_options
            .filter((option, position, options) => REASONING_ID.test(option.id) && option.id !== 'default'
              && options.findIndex((other) => other.id === option.id) === position)
            .map((option) => ({ value: option.id, label: option.label || option.id }))]}
          value={model.default_reasoning} onChange={(default_reasoning) => onChange({ default_reasoning })} /></div>
      </div>
      <div className="settings-fields model-capacity-fields">
        <label>最大输入 token<input inputMode="numeric" value={capacityText.max_input_tokens} placeholder="未设置"
          onChange={(event) => editCapacity('max_input_tokens', event.target.value)} />
          <span className="settings-field-help">模型支持的输入容量；超限由供应商判定，不自动截断历史。</span>
        </label>
        <label>最大输出 token<input inputMode="numeric" value={capacityText.max_output_tokens} placeholder="供应商默认"
          onChange={(event) => editCapacity('max_output_tokens', event.target.value)} />
          <span className="settings-field-help">每次模型请求的输出上限，留空不发送此参数。</span>
        </label>
      </div>
      <details className="settings-reasoning">
        <summary>推理选项{model.reasoning_options.length > 0 ? ` · ${model.reasoning_options.length} 项` : ''}</summary>
        <div className="settings-reasoning-heading">
          <p>按此型号支持的参数填写；“不发送”沿用供应商默认值。</p>
          <button type="button" className="settings-add" aria-label={`添加模型 ${title} 的推理选项`} disabled={model.reasoning_options.length >= 16}
            onClick={addReasoning}><Plus size={14} />添加选项</button>
        </div>
        {model.reasoning_options.map((option, index) => <div className="settings-fields reasoning-fields" key={option.localKey}>
          <label>选项 ID<input value={option.id} maxLength={64} spellCheck={false}
            onChange={(event) => editReasoning(index, { id: event.target.value })} placeholder="例如 high" /></label>
          <label>显示名<input value={option.label} maxLength={60}
            onChange={(event) => editReasoning(index, { label: event.target.value })} placeholder="例如 高" /></label>
          <div className="settings-field"><span>reasoning_effort</span><SelectionPopover ariaLabel={`模型 ${title} 推理选项 ${index + 1} 的 reasoning_effort`}
            options={EFFORT_VALUES.map((value) => ({ value, label: value || '不发送' }))}
            value={option.effort ?? ''} onChange={(effort) => editReasoning(index, { effort })} /></div>
          <div className="settings-field"><span>thinking.type</span><SelectionPopover ariaLabel={`模型 ${title} 推理选项 ${index + 1} 的 thinking.type`}
            options={THINKING_VALUES.map((value) => ({ value, label: value || '不发送' }))}
            value={option.thinking ?? ''} onChange={(thinking) => editReasoning(index, { thinking })} /></div>
          <button type="button" className="settings-remove" aria-label={`删除模型 ${title} 的推理选项 ${option.label || index + 1}`}
            onClick={() => removeReasoning(index)}><Trash2 size={15} /></button>
        </div>)}
      </details>
      <details className="settings-reasoning">
        <summary>高级设置</summary>
        <div className="settings-fields model-advanced-fields">
          <div className="settings-field"><span>输出上限参数</span><SelectionPopover ariaLabel={`模型 ${title} 的输出上限参数`}
            options={[{ value: 'max_tokens', label: 'max_tokens' }, { value: 'max_completion_tokens', label: 'max_completion_tokens' }]}
            value={model.output_token_parameter ?? 'max_tokens'}
            onChange={(value) => onChange({ output_token_parameter: value as ModelDefinitionConfig['output_token_parameter'] })} />
            <span className="settings-field-help">按供应商接口要求选择，仅在填写输出上限时发送。</span>
          </div>
        </div>
      </details>
      <div className="settings-model-actions">
        <button type="button" className={`settings-default${isDefault ? ' active' : ''}`} aria-pressed={isDefault} onClick={onDefault}>
          {isDefault ? '默认模型' : '设为默认模型'}
        </button>
        <button type="button" className="settings-model-remove" disabled={!removable} onClick={onRemove}>
          <Trash2 size={14} />删除模型
        </button>
      </div>
    </div>
  </details>
}
