import { Settings2 } from 'lucide-react'
import type { CatalogModel, ModelStatus } from '../types'
import SelectionPopover from './SelectionPopover'

interface Props {
  models: CatalogModel[]
  current: ModelStatus | null | undefined
  reasoning: string
  requiresImages: boolean
  disabled: boolean
  onSelect(model: string, reasoning?: string): void
  onSettings(): void
}

/** 下一轮模型设置；换模型时由服务端使用目标模型的默认项，不沿用旧强度。 */
export default function ComposerModelControls({ models, current, reasoning, requiresImages, disabled, onSelect, onSettings }: Props) {
  const options = current?.reasoning_options ?? []
  return <div className="composer-models">
    <SelectionPopover ariaLabel="切换聊天模型" value={current?.id ?? ''} disabled={disabled}
      options={models.map((model) => ({
        value: model.id,
        label: model.id,
        description: `${model.image_input ? '图文 VLM' : '文字 LLM'} · ${model.provider ?? model.destination ?? ''}${requiresImages && !model.image_input ? ' · 会话包含图片' : ''}${!model.configured ? ' · 未配置密钥' : ''}`,
        disabled: !model.configured || (requiresImages && !model.image_input),
      }))} onChange={(model) => onSelect(model)} />
    {(options.length > 0 || reasoning !== 'default') && <SelectionPopover ariaLabel="推理强度"
      value={reasoning} disabled={disabled} placeholder="原推理选项已移除，请重新选择"
      options={[{ value: 'default', label: '供应商默认' }, ...options.map((option) => ({ value: option.id, label: option.label }))]}
      onChange={(option) => { if (current?.id) onSelect(current.id, option) }} />}
    <button type="button" className="icon-button" aria-label="聊天模型设置" title="模型设置" disabled={disabled} onClick={onSettings}><Settings2 size={15} /></button>
  </div>
}
