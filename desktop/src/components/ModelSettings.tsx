import { useEffect, useRef, useState } from 'react'
import { Database, LoaderCircle, Plus, Search, Trash2, X } from 'lucide-react'
import { api } from '../api'
import type { ModelConfig, ModelConfigUpdate, ModelDefinitionConfig, ModelProviderConfig } from '../types'
import ModelEditor, { EFFORT_VALUES, REASONING_ID, THINKING_VALUES } from './ModelEditor'
import type { ModelDraft } from './ModelEditor'

type ProviderDraft = ModelProviderConfig & { api_key: string }
type ConfigDraft = { default_model: string; providers: ProviderDraft[]; models: ModelDraft[] }

interface ModelSettingsProps {
  onClose(): void
  onSaved(config: ModelConfig): void | Promise<void>
}

function fromConfig(config: ModelConfig): ConfigDraft {
  return {
    default_model: config.default_model ?? '',
    providers: config.providers.map((provider) => ({ ...provider, name: provider.name || provider.id, api_key: '' })),
    models: config.models.map((model) => ({
      ...model,
      reasoning_options: (model.reasoning_options ?? []).map((option) => ({ ...option, localKey: crypto.randomUUID() })),
      default_reasoning: model.default_reasoning ?? 'default',
      persisted: true,
    })),
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '无法读取或保存模型设置。'
}

/** 按供应商编辑连接和模型目录；API Key 仅在用户输入新值后发送，旧值不回显。 */
export default function ModelSettings({ onClose, onSaved }: ModelSettingsProps) {
  const [draft, setDraft] = useState<ConfigDraft | null>(null)
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null)
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null)
  const [modelSearch, setModelSearch] = useState('')
  const [expandedModels, setExpandedModels] = useState<Set<string>>(() => new Set())
  const [focusModelId, setFocusModelId] = useState<string | null>(null)
  const panelRef = useRef<HTMLElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
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
      if (event.key !== 'Tab' || event.defaultPrevented) return
      const panel = panelRef.current
      if (!panel) return
      const controls = [...panel.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), summary')]
        .filter((element) => element.getClientRects().length && !element.closest('[inert]'))
      if (!controls.length) return
      let active = document.activeElement
      // 下拉菜单位于 body portal；Tab 从它的触发按钮继续，不能跳到背后的聊天。
      if (active instanceof Element && active.closest('.selection-menu')) {
        const listId = active.closest('.selection-menu')?.querySelector('[role=listbox]')?.id
        active = controls.find((element) => element.getAttribute('aria-controls') === listId) ?? active
        const index = controls.indexOf(active as HTMLElement)
        event.preventDefault()
        controls[(index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length].focus()
      } else if (!panel.contains(active) || active === controls[event.shiftKey ? 0 : controls.length - 1]) {
        event.preventDefault()
        controls[event.shiftKey ? controls.length - 1 : 0].focus()
      }
    }
    document.addEventListener('keydown', onEscape)
    return () => document.removeEventListener('keydown', onEscape)
  }, [onClose, saving])

  useEffect(() => {
    let current = true
    api.modelConfig().then((config) => {
      if (!current) return
      setDraft(fromConfig(config))
      setSelectedProviderId(config.providers[0]?.id ?? null)
    }).catch((failure: unknown) => {
      if (current) setError(errorMessage(failure))
    }).finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [])

  const selectedProvider = draft?.providers.find((provider) => provider.id === selectedProviderId)
  const providerModels = draft?.models.filter((model) => model.provider === selectedProviderId) ?? []
  const query = modelSearch.trim().toLocaleLowerCase()
  const matchingModels = providerModels.filter((model) => `${model.upstream_model} ${model.name ?? ''}`.toLocaleLowerCase().includes(query))

  useEffect(() => {
    if (!focusModelId) return
    // 新建或校验失败时直接定位可编辑的模型，避免它藏在筛选结果或折叠区里。
    const input = panelRef.current?.querySelector<HTMLInputElement>(`[data-model-id="${CSS.escape(focusModelId)}"] input`)
    input?.focus()
    setFocusModelId(null)
  }, [focusModelId, draft])

  function selectProvider(id: string) {
    setSelectedProviderId(id)
    setModelSearch('')
  }

  function toggleModel(id: string) {
    setExpandedModels((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function editProvider(id: string, update: Partial<ProviderDraft>) {
    setDraft((old) => old && { ...old, providers: old.providers.map((provider) => provider.id === id ? { ...provider, ...update } : provider) })
    setError(null)
  }

  function editModel(id: string, update: Partial<ModelDraft>) {
    setDraft((old) => old && { ...old, models: old.models.map((model) => model.id === id ? { ...model, ...update } : model) })
    // 改型号或名称时不能因不再匹配搜索词而隐藏正在输入的行。
    setModelSearch('')
    setError(null)
  }

  function addProvider() {
    // 显示名称与内部身份分开；改名不会改变密钥引用或已有模型的归属。
    const id = `provider-${crypto.randomUUID().slice(0, 12)}`
    setDraft((old) => old && { ...old, providers: [...old.providers, {
      id, name: '', protocol: 'openai_chat_completions', base_url: '', key_configured: false, api_key: '',
    }] })
    selectProvider(id)
    setEditingProviderId(id)
    setError(null)
  }

  function removeProvider(id: string) {
    if (!draft || draft.models.some((model) => model.provider === id)) return
    const providers = draft.providers.filter((provider) => provider.id !== id)
    setDraft({ ...draft, providers })
    setSelectedProviderId(providers[0]?.id ?? null)
    setError(null)
  }

  function addModel() {
    if (!selectedProviderId) return
    const id = `model-${crypto.randomUUID().slice(0, 12)}`
    setDraft((old) => old && {
      ...old,
      default_model: old.default_model || id,
      models: [...old.models, {
        id, provider: selectedProviderId, name: '', upstream_model: '', image_input: false,
        reasoning_options: [], default_reasoning: 'default', persisted: false,
      }],
    })
    setModelSearch('')
    setFocusModelId(id)
    setError(null)
  }

  function removeModel(id: string) {
    setDraft((old) => {
      if (!old) return old
      const models = old.models.filter((model) => model.id !== id)
      return { ...old, models, default_model: old.default_model === id ? (models[0]?.id ?? '') : old.default_model }
    })
    setError(null)
  }

  async function save() {
    if (!draft || saving) return
    const providers = draft.providers.map((provider) => ({ ...provider, name: provider.name?.trim(), base_url: provider.base_url.trim() }))
    const models: ModelDefinitionConfig[] = draft.models.map((model) => ({
      id: model.id, provider: model.provider, upstream_model: model.upstream_model.trim(),
      ...(model.name?.trim() ? { name: model.name.trim() } : {}),
      image_input: model.image_input,
      ...(model.max_input_tokens !== undefined ? { max_input_tokens: model.max_input_tokens } : {}),
      ...(model.max_output_tokens !== undefined ? { max_output_tokens: model.max_output_tokens } : {}),
      ...(model.output_token_parameter ? { output_token_parameter: model.output_token_parameter } : {}),
      reasoning_options: model.reasoning_options.map((option) => ({
        id: option.id.trim(), label: option.label.trim(),
        ...(option.effort ? { effort: option.effort } : {}),
        ...(option.thinking ? { thinking: option.thinking } : {}),
      })),
      default_reasoning: model.default_reasoning.trim(),
    }))
    if (!providers.length || !models.length) { setError('请添加供应商，并至少配置一个模型。'); return }
    const incompleteProvider = providers.find((provider) => !provider.name || !provider.base_url)
    if (incompleteProvider) {
      selectProvider(incompleteProvider.id)
      setEditingProviderId(incompleteProvider.id)
      setError('请填写供应商名称和 API 地址。')
      return
    }
    for (const model of models) {
      const label = model.name || model.upstream_model || '新模型'
      const reject = (message: string) => {
        selectProvider(model.provider)
        setExpandedModels((old) => new Set(old).add(model.id))
        setFocusModelId(model.id)
        setError(message)
      }
      if (!model.upstream_model) { reject('请填写供应商提供的模型 ID。'); return }
      if ([model.max_input_tokens, model.max_output_tokens].some((value) => value !== undefined
        && (!Number.isInteger(value) || value < 1 || value > 2147483647))) {
        reject(`模型 ${label} 的 token 上限须为 1 至 2147483647 的整数。`)
        return
      }
      const options = model.reasoning_options
      if (options.length > 16) { reject(`模型 ${label} 最多可配置 16 个推理选项。`); return }
      if (options.some((option) => !REASONING_ID.test(option.id) || option.id === 'default'
        || !option.label || option.label.length > 60)) {
        reject(`模型 ${label} 的推理选项需要有效 ID 和显示名。ID 以字母开头，限 64 位字母、数字、下划线或连字符，不能使用 default。`)
        return
      }
      if (new Set(options.map((option) => option.id)).size !== options.length) {
        reject(`模型 ${label} 的推理选项 ID 不能重复。`); return
      }
      if (options.some((option) => !EFFORT_VALUES.includes(option.effort ?? '') || !THINKING_VALUES.includes(option.thinking ?? ''))) {
        reject(`模型 ${label} 的推理参数不受当前接口支持。`); return
      }
      if (options.some((option) => !option.effort && !option.thinking)) {
        reject(`模型 ${label} 的自定义推理选项至少要填写一个参数；都不发送时请使用“供应商默认”。`); return
      }
      if (options.some((option) => (option.thinking === 'disabled' && option.effort && option.effort !== 'none')
        || (option.thinking === 'enabled' && option.effort === 'none'))) {
        reject(`模型 ${label} 的推理选项存在冲突：关闭推理不能搭配强度值，启用推理不能搭配 none。`); return
      }
      if (model.default_reasoning !== 'default' && !options.some((option) => option.id === model.default_reasoning)) {
        reject(`请为模型 ${label} 选择有效的默认推理选项。`); return
      }
    }
    if (models.some((model) => !providers.some((provider) => provider.id === model.provider))
      || !models.some((model) => model.id === draft.default_model)) {
      setError('模型归属或默认模型无效，请重新选择。'); return
    }
    const update: ModelConfigUpdate = {
      default_model: draft.default_model,
      providers: providers.map((provider) => ({
        id: provider.id, name: provider.name, protocol: provider.protocol, base_url: provider.base_url,
        ...(provider.api_key ? { api_key: provider.api_key } : {}),
      })),
      models,
    }
    setSaving(true)
    setError(null)
    try {
      const saved = await api.saveModelConfig(update)
      setDraft(fromConfig(saved))
      await onSaved(saved)
      onClose()
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSaving(false)
    }
  }

  return <div className="settings-backdrop">
    <section ref={panelRef} className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="model-settings-heading">
      <header className="settings-header">
        <h2 id="model-settings-heading">设置</h2>
        <button ref={closeButtonRef} type="button" className="icon-button" aria-label="关闭模型设置" onClick={onClose} disabled={saving}><X size={18} /></button>
      </header>
      <div className="settings-content" inert={saving}>
        {loading && <div className="settings-state"><LoaderCircle className="spin" size={17} />正在读取配置…</div>}
        {!loading && !draft && <div className="settings-state">配置暂时无法读取，请关闭后重试。</div>}
        {draft && <div className="settings-provider-layout">
          <nav className="settings-provider-nav" aria-label="模型供应商">
            <div className="settings-nav-section"><Database size={18} /><span>模型</span></div>
            <div className="settings-provider-nav-heading"><span>供应商</span><button type="button" className="icon-button" aria-label="添加供应商" title="添加供应商" onClick={addProvider}><Plus size={16} /></button></div>
            <div className="settings-provider-list">
              {draft.providers.map((provider) => <button type="button" key={provider.id}
                className={`settings-provider-choice${selectedProviderId === provider.id ? ' active' : ''}`}
                aria-current={selectedProviderId === provider.id ? 'true' : undefined} onClick={() => selectProvider(provider.id)}>
                <span>{provider.name?.trim() || '新供应商'}</span>
                <small>{draft.models.filter((model) => model.provider === provider.id).length} 个模型</small>
              </button>)}
            </div>
          </nav>
          <div className="settings-provider-detail">
            {!selectedProvider && <div className="settings-provider-empty"><p>添加供应商后，填写 API 地址和密钥。</p><button type="button" className="secondary-button" onClick={addProvider}><Plus size={15} />添加供应商</button></div>}
            {selectedProvider && <div className="settings-provider-card">
              <div className="settings-provider-card-heading">
                <h3 id="provider-heading">{selectedProvider.name?.trim() || '新供应商'}</h3>
                <button type="button" className="settings-edit-provider" aria-expanded={editingProviderId === selectedProvider.id}
                  onClick={() => setEditingProviderId(editingProviderId === selectedProvider.id ? null : selectedProvider.id)}>编辑名称</button>
              </div>
              <div className="settings-provider-surface">
                <section className="settings-section" aria-labelledby="provider-heading">
                  <div className="settings-provider-edit" hidden={editingProviderId !== selectedProvider.id}>
                    <label>供应商名称<input value={selectedProvider.name ?? ''} maxLength={128}
                      onChange={(event) => editProvider(selectedProvider.id, { name: event.target.value })} placeholder="例如 火山引擎" /></label>
                    <button type="button" className="settings-remove" aria-label={`删除供应商 ${selectedProvider.name || '新供应商'}`}
                      title={providerModels.length ? '先删除此供应商下的模型' : '删除供应商'} disabled={providerModels.length > 0}
                      onClick={() => removeProvider(selectedProvider.id)}><Trash2 size={15} /></button>
                  </div>
                  <div className="settings-fields provider-fields">
                    <label>API 密钥<input value={selectedProvider.api_key} type="password" autoComplete="new-password" spellCheck={false}
                      onChange={(event) => editProvider(selectedProvider.id, { api_key: event.target.value })}
                      placeholder={selectedProvider.key_configured ? '已配置，输入新值可替换' : '输入 API Key'} /></label>
                  </div>
                  <details className="settings-connection" open key={selectedProvider.id}>
                    <summary>自定义设置</summary>
                    <div className="settings-fields provider-fields">
                      <label>API 地址<input value={selectedProvider.base_url} type="url" spellCheck={false}
                        onChange={(event) => editProvider(selectedProvider.id, { base_url: event.target.value })} placeholder="例如 https://ark.cn-beijing.volces.com/api/v3" /></label>
                    </div>
                    <p className="settings-protocol-note">填写兼容 OpenAI Chat Completions 的 API 地址。</p>
                  </details>
                </section>
                <section className="settings-section" aria-labelledby="models-heading">
                  <div className="settings-section-heading"><h3 id="models-heading">模型目录 <span className="settings-model-count">{providerModels.length}</span></h3></div>
                  <div className="settings-model-search"><Search size={17} /><input aria-label="搜索模型" placeholder="搜索模型" value={modelSearch}
                    onChange={(event) => setModelSearch(event.target.value)} />
                    {modelSearch && <button type="button" aria-label="清空模型搜索" onClick={() => setModelSearch('')}><X size={14} /></button>}
                  </div>
                  {providerModels.length === 0 && <p className="settings-model-empty">添加供应商提供的模型或推理接入点。</p>}
                  {providerModels.length > 0 && matchingModels.length === 0 && <p className="settings-model-empty">没有匹配的模型。</p>}
                  {/* 筛选只隐藏行，不删除草稿或重置已展开的高级字段。 */}
                  {providerModels.map((model) => <div key={model.id} data-model-id={model.id} hidden={!matchingModels.includes(model)}><ModelEditor model={model}
                    expanded={expandedModels.has(model.id)} onToggle={() => toggleModel(model.id)}
                    isDefault={draft.default_model === model.id} removable={draft.models.length > 1 || !model.persisted}
                    onChange={(update) => editModel(model.id, update)} onRemove={() => removeModel(model.id)}
                    onDefault={() => setDraft((old) => old && { ...old, default_model: model.id })} /></div>)}
                </section>
              </div>
            </div>}
          </div>
        </div>}
      </div>
      <footer className="settings-footer">
        {error && <p className="settings-error" role="alert">{error}</p>}
        <button type="button" className="settings-footer-add" onClick={addModel} disabled={!selectedProvider || saving}><Plus size={15} />添加模型</button>
        <button type="button" className="secondary-button" onClick={onClose} disabled={saving}>取消</button>
        <button type="button" className="primary-button" onClick={save} disabled={!draft || saving}>
          {saving && <LoaderCircle className="spin" size={15} />}保存
        </button>
      </footer>
    </section>
  </div>
}
