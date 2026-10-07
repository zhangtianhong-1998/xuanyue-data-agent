import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Search, Settings2 } from 'lucide-react'
import type { CatalogModel, ModelStatus } from '../types'

interface Props {
  models: CatalogModel[]
  current: ModelStatus | null | undefined
  reasoning: string
  requiresImages: boolean
  disabled: boolean
  onSelect(model: string, reasoning?: string): void
  onSettings(): void
}

/** 输入框底部的下一轮模型选择；模型与档位共用菜单，供应商配置从菜单进入。 */
export default function ComposerModelControls({ models, current, reasoning, requiresImages, disabled, onSelect, onSettings }: Props) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(-1)
  const [position, setPosition] = useState<{ left: number; width: number; maxHeight: number; top?: number; bottom?: number }>({
    left: 0, width: 320, maxHeight: 520,
  })
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const pendingSelectionRef = useRef<{ sourceModel: string | null | undefined; targetModel: string; sawDisabled: boolean } | null>(null)
  const menuId = useId()
  const listId = `${menuId}-models`
  const options = current?.reasoning_options ?? []
  const selectedReasoning = options.find((option) => option.id === reasoning)
  const missingReasoning = reasoning !== 'default' && !selectedReasoning
  const filtered = filterModels(query)
  const selectable = filtered.map((model, index) => unavailableReason(model) ? -1 : index).filter((index) => index >= 0)

  function unavailableReason(model: CatalogModel) {
    if (!model.configured) return '未配置密钥'
    if (requiresImages && !model.image_input) return '当前对话包含图片，需选择图文模型'
    return ''
  }

  function filterModels(value: string) {
    const search = value.trim().toLocaleLowerCase()
    return models.filter((model) => `${model.id} ${model.provider ?? ''} ${model.image_input ? '图文 VLM' : '文字 LLM'}`
      .toLocaleLowerCase().includes(search))
  }

  function closeMenu(restoreFocus = false) {
    setOpen(false)
    setQuery('')
    if (restoreFocus) triggerRef.current?.focus()
  }

  // 等待服务端保存时，菜单不能继续提交第二个选择；外部切换会话也会关闭旧菜单。
  useEffect(() => {
    setOpen(false)
    setQuery('')
  }, [current?.id, reasoning, disabled])

  useEffect(() => {
    const pending = pendingSelectionRef.current
    if (!pending) return
    if (current?.id !== pending.sourceModel && current?.id !== pending.targetModel) {
      pendingSelectionRef.current = null
      return
    }
    if (disabled) { pending.sawDisabled = true; return }
    if (!pending.sawDisabled) return
    pendingSelectionRef.current = null
    // 保存时按钮暂时禁用会令焦点落回 body；仅修复这次失焦，不打断用户新的操作。
    if (document.activeElement === document.body) triggerRef.current?.focus()
  }, [disabled, current?.id])

  useEffect(() => {
    function userMoved(event: Event) {
      if (!pendingSelectionRef.current) return
      if (event.type === 'focusin' && (event.target === document.body || event.target === triggerRef.current)) return
      pendingSelectionRef.current = null
    }
    document.addEventListener('pointerdown', userMoved)
    document.addEventListener('focusin', userMoved)
    document.addEventListener('keydown', userMoved, true)
    return () => {
      document.removeEventListener('pointerdown', userMoved)
      document.removeEventListener('focusin', userMoved)
      document.removeEventListener('keydown', userMoved, true)
    }
  }, [])

  useEffect(() => {
    if (open && activeIndex >= 0) document.getElementById(`${listId}-${activeIndex}`)?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open, query, listId])

  useEffect(() => {
    if (!open) return
    function outside(event: Event) {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target) && !triggerRef.current?.contains(event.target)) {
        setOpen(false)
      }
    }
    function resized() { setOpen(false) }
    // 菜单通过 portal 避开输入框的裁切；父容器滚动后关闭，防止锚点错位。
    document.addEventListener('pointerdown', outside)
    document.addEventListener('focusin', outside)
    document.addEventListener('scroll', outside, true)
    window.addEventListener('resize', resized)
    searchRef.current?.focus()
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('focusin', outside)
      document.removeEventListener('scroll', outside, true)
      window.removeEventListener('resize', resized)
    }
  }, [open])

  function openMenu() {
    if (disabled) return
    const rect = triggerRef.current?.getBoundingClientRect()
    if (!rect) return
    const margin = 8
    const width = Math.min(320, window.innerWidth - margin * 2)
    const above = rect.top - margin * 2
    const below = window.innerHeight - rect.bottom - margin * 2
    const placeAbove = above >= 240 || above >= below
    setPosition({
      left: Math.max(margin, Math.min(rect.right - width, window.innerWidth - width - margin)),
      width,
      maxHeight: Math.max(0, Math.min(520, placeAbove ? above : below)),
      ...(placeAbove ? { bottom: window.innerHeight - rect.top + margin } : { top: rect.bottom + margin }),
    })
    setQuery('')
    const selected = models.findIndex((model) => model.id === current?.id && !unavailableReason(model))
    setActiveIndex(selected >= 0 ? selected : models.findIndex((model) => !unavailableReason(model)))
    setOpen(true)
  }

  function chooseModel(model: CatalogModel) {
    if (unavailableReason(model)) return
    closeMenu(true)
    // 重选当前模型不重置档位；换模型时由后端采用目标模型自己的默认档位。
    if (model.id !== current?.id) {
      pendingSelectionRef.current = { sourceModel: current?.id, targetModel: model.id, sawDisabled: false }
      onSelect(model.id)
    }
  }

  function chooseReasoning(value: string) {
    closeMenu(true)
    if (current?.id && value !== reasoning) {
      pendingSelectionRef.current = { sourceModel: current.id, targetModel: current.id, sawDisabled: false }
      onSelect(current.id, value)
    }
  }

  function handleSearchKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!selectable.length) return
      const active = selectable.indexOf(activeIndex)
      const direction = event.key === 'ArrowDown' ? 1 : -1
      const next = active < 0 ? (direction === 1 ? 0 : selectable.length - 1) : (active + direction + selectable.length) % selectable.length
      setActiveIndex(selectable[next])
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const model = filtered[activeIndex]
      if (model) chooseModel(model)
    }
  }

  return <div className="composer-models">
    <button ref={triggerRef} type="button" className="model-switch-trigger" disabled={disabled}
      aria-label={`切换模型与推理强度，当前${current?.id ?? '未选择模型'}${reasoning !== 'default' ? `，${selectedReasoning?.label ?? '原推理选项已移除'}` : ''}`}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? menuId : undefined}
      onClick={() => open ? closeMenu() : openMenu()}
      onKeyDown={(event) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); openMenu() }
      }}>
      <span className="model-switch-name">{current?.id ?? '选择模型'}</span>
      {reasoning !== 'default' && <span className="model-switch-reasoning">{selectedReasoning?.label ?? '选项已移除'}</span>}
      <ChevronDown size={14} aria-hidden="true" />
    </button>
    {open && createPortal(<div id={menuId} ref={menuRef} className="model-switch-menu" style={position}
      role="dialog" aria-label="模型与推理设置" onKeyDown={(event) => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu(true) }
      }}>
      <div className="model-switch-search">
        <Search size={15} aria-hidden="true" />
        <input ref={searchRef} type="search" value={query} placeholder="搜索模型" aria-label="搜索模型"
          role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls={listId}
          aria-activedescendant={filtered[activeIndex] && !unavailableReason(filtered[activeIndex]) ? `${listId}-${activeIndex}` : undefined}
          onKeyDown={handleSearchKey} onChange={(event) => {
            const value = event.target.value
            setQuery(value)
            setActiveIndex(filterModels(value).findIndex((model) => !unavailableReason(model)))
          }} />
      </div>
      <div className="model-switch-section-label">模型</div>
      <div id={listId} role="listbox" aria-label="聊天模型" className="model-switch-options">
        {filtered.length ? filtered.map((model, index) => {
          const unavailable = unavailableReason(model)
          return <div key={model.id} id={`${listId}-${index}`} role="option" tabIndex={-1}
            aria-selected={model.id === current?.id} aria-disabled={Boolean(unavailable)}
            className={`model-switch-option${activeIndex === index ? ' active' : ''}${unavailable ? ' disabled' : ''}`}
            onMouseEnter={() => { if (!unavailable) setActiveIndex(index) }}
            onMouseDown={(event) => event.preventDefault()} onClick={() => chooseModel(model)}>
            <span className="model-switch-option-copy"><span>{model.id}</span>
              <small>{unavailable || model.provider || '自定义供应商'}</small>
            </span>
            <span className="model-switch-capability">{model.image_input ? '图文' : '文字'}</span>
            {model.id === current?.id && <Check size={15} aria-hidden="true" />}
          </div>
        }) : <div className="model-switch-empty">{models.length ? '没有匹配的模型' : '还没有配置模型'}</div>}
      </div>
      {current?.id && (options.length > 0 || missingReasoning) && <div className="model-switch-reasoning-options">
        <div className="model-switch-section-label">推理强度</div>
        {missingReasoning && <p className="model-switch-warning">原推理选项已移除，请重新选择。</p>}
        {[{ id: 'default', label: '供应商默认' }, ...options].map((option) => <button type="button" key={option.id}
          className={`model-switch-reasoning-option${option.id === reasoning ? ' selected' : ''}`}
          aria-pressed={option.id === reasoning} onClick={() => chooseReasoning(option.id)}>
          <span>{option.label}</span>{option.id === reasoning && <Check size={14} aria-hidden="true" />}
        </button>)}
      </div>}
      <button type="button" className="model-switch-settings" onClick={() => { closeMenu(true); onSettings() }}>
        <Settings2 size={15} aria-hidden="true" /><span>模型设置</span>
      </button>
    </div>, document.body)}
  </div>
}
