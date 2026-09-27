import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, Plus, Trash2, X } from 'lucide-react'
import { api } from '../api'
import type { ModelConfig, ModelConfigUpdate, ModelDefinitionConfig, ModelProviderConfig } from '../types'
import SelectionPopover from './SelectionPopover'

type ProviderDraft = ModelProviderConfig & { api_key: string; persisted: boolean; localKey: string }
type ModelDraft = ModelDefinitionConfig & { persisted: boolean; localKey: string }
type ConfigDraft = { default_model: string; providers: ProviderDraft[]; models: ModelDraft[] }

interface ModelSettingsProps {
  onClose(): void
  onSaved(config: ModelConfig): void | Promise<void>
}

function fromConfig(config: ModelConfig): ConfigDraft {
  return {
    ...config,
    default_model: config.default_model ?? '',
    providers: config.providers.map((provider) => ({ ...provider, api_key: '', persisted: true, localKey: `provider:${provider.id}` })),
    models: config.models.map((model) => ({ ...model, persisted: true, localKey: `model:${model.id}` })),
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

  function editModel(index: number, update: Partial<ModelDefinitionConfig>) {
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
              <label className="settings-checkbox"><input type="checkbox" checked={model.image_input}
                onChange={(event) => editModel(index, { image_input: event.target.checked })} />允许图片输入</label>
            </div>)}
            <p className="settings-capability-note">图片输入是手动声明的能力，仍需用所选模型实际验证。</p>
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
