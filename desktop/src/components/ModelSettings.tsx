import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, Plus, Trash2, X } from 'lucide-react'
import { api } from '../api'
import type { ModelConfig, ModelConfigUpdate, ModelDefinitionConfig, ModelProviderConfig, ReasoningOption } from '../types'
import SelectionPopover from './SelectionPopover'

type ProviderDraft = ModelProviderConfig & { api_key: string; persisted: boolean; localKey: string }
type ReasoningDraft = ReasoningOption & { localKey: string }
type ModelDraft = Omit<ModelDefinitionConfig, 'reasoning_options'> & {
  reasoning_options: ReasoningDraft[]; persisted: boolean; localKey: string
}
type ConfigDraft = { default_model: string; providers: ProviderDraft[]; models: ModelDraft[] }

const EFFORT_VALUES = ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
const THINKING_VALUES = ['', 'enabled', 'disabled', 'auto']
const REASONING_ID = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/

interface ModelSettingsProps {
  onClose(): void
  onSaved(config: ModelConfig): void | Promise<void>
}

function fromConfig(config: ModelConfig): ConfigDraft {
  return {
    ...config,
    default_model: config.default_model ?? '',
    providers: config.providers.map((provider) => ({ ...provider, api_key: '', persisted: true, localKey: `provider:${provider.id}` })),
    // 旧服务没有推理配置；保留供应商默认行为，不替用户猜一个强度。
    models: config.models.map((model) => ({
      ...model,
      reasoning_options: (model.reasoning_options ?? []).map((option) => ({ ...option, localKey: crypto.randomUUID() })),
      default_reasoning: model.default_reasoning ?? 'default',
      persisted: true, localKey: `model:${model.id}`,
    })),
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '无法读取或保存模型设置。'
}

function uniqueId(prefix: string, used: string[]): string {
  let number = 1
  while (used.includes(`${prefix}-${number}`)) number += 1
  return `${prefix}-${number}`
}

/** 编辑产品模型目录；只在保存时发送新密钥，页面和响应都不保留旧密钥。 */
export default function ModelSettings({ onClose, onSaved }: ModelSettingsProps) {
  const [draft, setDraft] = useState<ConfigDraft | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // 弹窗关闭后回到触发入口；从已卸载的新建会话弹窗进入时回到侧栏设置按钮。
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeButtonRef.current?.focus()
    return () => {
      if (opener && opener !== document.body && opener.isConnected) opener.focus()
      else document.querySelector<HTMLElement>('.sidebar-footer button')?.focus()
    }
  }, [])

  useEffect(() => {
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && !saving) onClose()
    }
    document.addEventListener('keydown', onEscape)
    return () => document.removeEventListener('keydown', onEscape)
  }, [onClose, saving])

  useEffect(() => {
    let current = true
    api.modelConfig().then((config) => {
      if (!current) return
      setDraft(fromConfig(config))
    }).catch((failure: unknown) => {
      if (current) setError(errorMessage(failure))
    }).finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [])

  function editProvider(index: number, update: Partial<ProviderDraft>) {
    setDraft((old) => {
      if (!old) return old
      const previousId = old.providers[index]?.id
      return {
        ...old,
        providers: old.providers.map((provider, position) => position === index ? { ...provider, ...update } : provider),
        // 新供应商改 ID 时，已选它的模型也要随之更新。
        models: update.id && previousId && update.id !== previousId
          ? old.models.map((model) => model.provider === previousId ? { ...model, provider: update.id! } : model)
          : old.models,
      }
    })
    setError(null)
  }

  function editModel(index: number, update: Partial<ModelDraft>) {
    setDraft((old) => {
      if (!old) return old
      const previousId = old.models[index]?.id
      return {
        ...old,
        models: old.models.map((model, position) => position === index ? { ...model, ...update } : model),
        default_model: update.id && old.default_model === previousId ? update.id : old.default_model,
      }
    })
    setError(null)
  }

  function editReasoning(modelIndex: number, optionIndex: number, update: Partial<ReasoningOption>) {
    const model = draft?.models[modelIndex]
    if (!model) return
    const previous = model.reasoning_options[optionIndex]
    editModel(modelIndex, {
      reasoning_options: model.reasoning_options.map((option, index) => index === optionIndex ? { ...option, ...update } : option),
      // 修改默认项的 ID 后仍指向同一项；保存前再校验最终 ID。
      default_reasoning: update.id !== undefined && model.default_reasoning === previous.id ? update.id : model.default_reasoning,
    })
  }

  function addReasoning(modelIndex: number) {
    const model = draft?.models[modelIndex]
    if (!model || model.reasoning_options.length >= 16) return
    const used = model.reasoning_options.map((option) => option.id)
    const id = used.includes('high') ? uniqueId('high', used) : 'high'
    editModel(modelIndex, { reasoning_options: [...model.reasoning_options, {
      id, label: id, effort: 'high', localKey: crypto.randomUUID(),
    }] })
  }

  function removeReasoning(modelIndex: number, optionIndex: number) {
    const model = draft?.models[modelIndex]
    if (!model) return
    editModel(modelIndex, {
      reasoning_options: model.reasoning_options.filter((_, index) => index !== optionIndex),
      default_reasoning: model.default_reasoning === model.reasoning_options[optionIndex].id ? 'default' : model.default_reasoning,
    })
  }

  function addProvider() {
    setDraft((old) => old && {
      ...old,
      providers: [...old.providers, {
        id: uniqueId('provider', old.providers.map((provider) => provider.id)),
        protocol: 'openai_chat_completions', base_url: '', key_configured: false, api_key: '',
        persisted: false, localKey: `new-provider:${crypto.randomUUID()}`,
      }],
    })
  }

  function removeProvider(index: number) {
    setDraft((old) => old && { ...old, providers: old.providers.filter((_, position) => position !== index) })
  }

  function addModel() {
    setDraft((old) => {
      if (!old) return old
      const id = uniqueId('model', old.models.map((model) => model.id))
      return {
        ...old,
        default_model: old.default_model || id,
        models: [...old.models, {
          id, provider: old.providers[0]?.id ?? '', upstream_model: '', image_input: false,
          reasoning_options: [], default_reasoning: 'default',
          persisted: false, localKey: `new-model:${crypto.randomUUID()}`,
        }],
      }
    })
  }

  function removeModel(index: number) {
    setDraft((old) => {
      if (!old) return old
      const models = old.models.filter((_, position) => position !== index)
      return { ...old, models, default_model: models.some((model) => model.id === old.default_model)
        ? old.default_model : (models[0]?.id ?? '') }
    })
  }

  async function save() {
    if (!draft || saving) return
    const providers = draft.providers.map((provider) => ({ ...provider, id: provider.id.trim(), base_url: provider.base_url.trim() }))
    const models: ModelDefinitionConfig[] = draft.models.map((model) => ({
      id: model.id.trim(), provider: model.provider.trim(), upstream_model: model.upstream_model.trim(),
      image_input: model.image_input,
      reasoning_options: model.reasoning_options.map((option) => ({
        id: option.id.trim(), label: option.label.trim(),
        ...(option.effort ? { effort: option.effort } : {}),
        ...(option.thinking ? { thinking: option.thinking } : {}),
      })),
      default_reasoning: model.default_reasoning.trim(),
    }))
    if (!providers.length || !models.length) { setError('至少保留一个供应商和一个模型。'); return }
    if (providers.some((provider) => !provider.id || !provider.base_url)
      || models.some((model) => !model.id || !model.provider || !model.upstream_model)) {
      setError('请填写完整的供应商 ID、接口地址、模型 ID 和上游模型名。')
      return
    }
    if (new Set(providers.map((provider) => provider.id)).size !== providers.length
      || new Set(models.map((model) => model.id)).size !== models.length) {
      setError('供应商 ID 和模型 ID 各自不能重复。')
      return
    }
    for (const model of models) {
      const options = model.reasoning_options
      if (options.length > 16) {
        setError(`模型 ${model.id} 最多可配置 16 个推理选项。`)
        return
      }
      if (options.some((option) => !REASONING_ID.test(option.id) || option.id === 'default'
        || !option.label || option.label.length > 60)) {
        setError(`模型 ${model.id} 的推理选项需要有效 ID 和显示名。ID 以字母开头，限 64 位字母、数字、下划线或连字符，不能使用 default。`)
        return
      }
      if (new Set(options.map((option) => option.id)).size !== options.length) {
        setError(`模型 ${model.id} 的推理选项 ID 不能重复。`)
        return
      }
      if (options.some((option) => !EFFORT_VALUES.includes(option.effort ?? '') || !THINKING_VALUES.includes(option.thinking ?? ''))) {
        setError(`模型 ${model.id} 的推理参数不受当前接口支持。`)
        return
      }
      if (options.some((option) => !option.effort && !option.thinking)) {
        setError(`模型 ${model.id} 的自定义推理选项至少要填写一个参数；都不发送时请使用“供应商默认”。`)
        return
      }
      if (options.some((option) => (option.thinking === 'disabled' && option.effort && option.effort !== 'none')
        || (option.thinking === 'enabled' && option.effort === 'none'))) {
        setError(`模型 ${model.id} 的推理选项存在冲突：关闭推理不能搭配强度值，启用推理不能搭配 none。`)
        return
      }
      if (model.default_reasoning !== 'default' && !options.some((option) => option.id === model.default_reasoning)) {
        setError(`请为模型 ${model.id} 选择有效的默认推理选项。`)
        return
      }
    }
    const defaultModel = draft.default_model.trim()
    if (models.some((model) => !providers.some((provider) => provider.id === model.provider))
      || !models.some((model) => model.id === defaultModel)) {
      setError('请为每个模型选择供应商，并指定一个有效的默认模型。')
      return
    }
    const update: ModelConfigUpdate = {
      default_model: defaultModel,
      providers: providers.map((provider) => ({
        id: provider.id, protocol: provider.protocol, base_url: provider.base_url,
        ...(provider.api_key ? { api_key: provider.api_key } : {}),
      })),
      models,
    }
    setSaving(true)
    setError(null)
    try {
      const saved = await api.saveModelConfig(update)
      // 服务端返回的目录没有密钥；先清掉输入框，再通知外层刷新模型可用状态。
      setDraft(fromConfig(saved))
      await onSaved(saved)
      onClose()
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSaving(false)
    }
  }

  return <div className="settings-backdrop" onMouseDown={(event) => { if (!saving && event.target === event.currentTarget) onClose() }}>
    <section className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="model-settings-heading">
      <header className="settings-header">
        <div>
          <h2 id="model-settings-heading">模型设置</h2>
          <p>管理本机模型目录。当前支持 OpenAI Chat Completions 兼容接口。</p>
        </div>
        <button ref={closeButtonRef} type="button" className="icon-button" aria-label="关闭模型设置" onClick={onClose} disabled={saving}><X size={18} /></button>
      </header>
      <div className="settings-content" inert={saving}>
        {loading && <div className="settings-state"><LoaderCircle className="spin" size={17} />正在读取配置…</div>}
        {!loading && !draft && <div className="settings-state">配置暂时无法读取。请检查本机服务后重试。</div>}
        {draft && <>
          <section className="settings-section" aria-labelledby="provider-heading">
            <div className="settings-section-heading">
              <div><h3 id="provider-heading">供应商接口</h3><p>接口地址指向服务商的 OpenAI 兼容 API。已有密钥只显示状态。</p></div>
              <button type="button" className="settings-add" onClick={addProvider}><Plus size={15} />添加</button>
            </div>
            {draft.providers.map((provider, index) => {
              const used = draft.models.some((model) => model.provider === provider.id)
              return <div className="settings-item" key={provider.localKey}>
                <div className="settings-item-heading">
                  <strong>{provider.id || '新供应商'}</strong>
                  <span className={`settings-key-state${provider.key_configured || provider.api_key ? ' configured' : ''}`}>
                    {provider.key_configured || provider.api_key ? '密钥已配置' : '未配置密钥'}
                  </span>
                  <button type="button" className="settings-remove" aria-label={`删除供应商 ${provider.id}`} title={used ? '请先将关联模型改选其他供应商' : undefined}
                    disabled={draft.providers.length <= 1 || used} onClick={() => removeProvider(index)}><Trash2 size={15} /></button>
                </div>
                <div className="settings-fields provider-fields">
                  <label>供应商 ID<input value={provider.id} maxLength={64} readOnly={provider.persisted}
                    onChange={(event) => editProvider(index, { id: event.target.value })} placeholder="例如 ark" /></label>
                  <label>接口地址<input value={provider.base_url} type="url" spellCheck={false}
                    onChange={(event) => editProvider(index, { base_url: event.target.value })} placeholder="https://…/v1" /></label>
                  <label>API Key<input value={provider.api_key} type="password" autoComplete="new-password" spellCheck={false}
                    onChange={(event) => editProvider(index, { api_key: event.target.value })}
                    placeholder={provider.key_configured ? '留空则保留已保存密钥' : '输入密钥'} /></label>
                </div>
              </div>
            })}
          </section>
          <section className="settings-section" aria-labelledby="models-heading">
            <div className="settings-section-heading">
              <div><h3 id="models-heading">模型</h3><p>会话保留模型 ID；改动该 ID 的接口或上游模型会影响后续提问。</p></div>
              <button type="button" className="settings-add" onClick={addModel}><Plus size={15} />添加</button>
            </div>
            {draft.models.map((model, index) => <div className="settings-item" key={model.localKey}>
              <div className="settings-item-heading">
                <strong>{model.id || '新模型'}</strong>
                <button type="button" className={`settings-default${draft.default_model === model.id ? ' active' : ''}`}
                  aria-pressed={draft.default_model === model.id} onClick={() => setDraft((old) => old && { ...old, default_model: model.id })}>
                  {draft.default_model === model.id ? '默认模型' : '设为默认'}
                </button>
                <button type="button" className="settings-remove" aria-label={`删除模型 ${model.id}`} disabled={draft.models.length <= 1}
                  onClick={() => removeModel(index)}><Trash2 size={15} /></button>
              </div>
              <div className="settings-fields model-fields">
                <label>模型 ID<input value={model.id} maxLength={128} readOnly={model.persisted}
                  onChange={(event) => editModel(index, { id: event.target.value })} placeholder="例如 analysis" /></label>
                <div className="settings-field"><span>供应商</span><SelectionPopover ariaLabel={`模型 ${model.id} 的供应商`}
                  options={draft.providers.map((provider) => ({ value: provider.id, label: provider.id }))}
                  value={model.provider} onChange={(provider) => editModel(index, { provider })} /></div>
                <label>上游模型名<input value={model.upstream_model} spellCheck={false}
                  onChange={(event) => editModel(index, { upstream_model: event.target.value })} placeholder="服务商要求的模型 ID" /></label>
              </div>
              <div className="settings-fields model-capability-fields">
                <div className="settings-field"><span>模型类型</span><SelectionPopover ariaLabel={`模型 ${model.id} 的类型`}
                  options={[{ value: 'text', label: '文字 LLM' }, { value: 'image', label: '图文 VLM' }]}
                  value={model.image_input ? 'image' : 'text'} onChange={(value) => editModel(index, { image_input: value === 'image' })} /></div>
                <div className="settings-field"><span>默认推理选项</span><SelectionPopover ariaLabel={`模型 ${model.id} 的默认推理选项`}
                  options={[{ value: 'default', label: '供应商默认' }, ...model.reasoning_options
                    .filter((option, position, options) => REASONING_ID.test(option.id) && option.id !== 'default'
                      && options.findIndex((other) => other.id === option.id) === position)
                    .map((option) => ({ value: option.id, label: option.label || option.id }))]}
                  value={model.default_reasoning} onChange={(default_reasoning) => editModel(index, { default_reasoning })} /></div>
              </div>
              <details className="settings-reasoning">
                <summary>推理选项{model.reasoning_options.length > 0 ? ` · ${model.reasoning_options.length} 项` : ''}</summary>
                <div className="settings-reasoning-heading">
                  <p>按此型号支持的参数填写；“不发送”沿用服务端行为，不等于关闭推理。</p>
                  <button type="button" className="settings-add" aria-label={`添加模型 ${model.id} 的推理选项`} disabled={model.reasoning_options.length >= 16}
                    title={model.reasoning_options.length >= 16 ? '每个模型最多配置 16 项' : undefined} onClick={() => addReasoning(index)}><Plus size={15} />添加选项</button>
                </div>
                {model.reasoning_options.map((option, optionIndex) => <div className="settings-fields reasoning-fields" key={option.localKey}>
                  <label>选项 ID<input value={option.id} maxLength={64} spellCheck={false}
                    onChange={(event) => editReasoning(index, optionIndex, { id: event.target.value })} placeholder="例如 high" /></label>
                  <label>显示名<input value={option.label} maxLength={60}
                    onChange={(event) => editReasoning(index, optionIndex, { label: event.target.value })} placeholder="例如 高" /></label>
                  <div className="settings-field"><span>reasoning_effort</span><SelectionPopover ariaLabel={`模型 ${model.id} 推理选项 ${optionIndex + 1} 的 reasoning_effort`}
                    options={EFFORT_VALUES.map((value) => ({ value, label: value || '不发送' }))}
                    value={option.effort ?? ''} onChange={(effort) => editReasoning(index, optionIndex, { effort })} /></div>
                  <div className="settings-field"><span>thinking.type</span><SelectionPopover ariaLabel={`模型 ${model.id} 推理选项 ${optionIndex + 1} 的 thinking.type`}
                    options={THINKING_VALUES.map((value) => ({ value, label: value || '不发送' }))}
                    value={option.thinking ?? ''} onChange={(thinking) => editReasoning(index, optionIndex, { thinking })} /></div>
                  <button type="button" className="settings-remove" aria-label={`删除模型 ${model.id} 的推理选项 ${option.label || optionIndex + 1}`}
                    onClick={() => removeReasoning(index, optionIndex)}><Trash2 size={15} /></button>
                </div>)}
              </details>
            </div>)}
            <p className="settings-capability-note">图片与推理能力由你声明，需按具体服务验证；暂不支持要求回传私有思考内容才能继续调用工具的模式。</p>
          </section>
        </>}
      </div>
      <footer className="settings-footer">
        <div className="settings-footer-info">
          {error && <p className="settings-error" role="alert">{error}</p>}
          <p className="settings-save-note">保存会重写本机 xuanyue.toml，原有注释会丢失。</p>
        </div>
        <button type="button" className="secondary-button" onClick={onClose} disabled={saving}>取消</button>
        <button type="button" className="primary-button" onClick={save} disabled={!draft || saving}>
          {saving && <LoaderCircle className="spin" size={15} />}保存配置
        </button>
      </footer>
    </section>
  </div>
}
