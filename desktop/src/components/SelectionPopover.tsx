import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Search } from 'lucide-react'

export interface SelectionOption {
  value: string
  label: string
  description?: string
  disabled?: boolean
}

interface SelectionPopoverProps {
  id?: string
  ariaLabel: string
  options: SelectionOption[]
  value: string
  onChange(value: string): void
  placeholder?: string
  disabled?: boolean
}

/** 供模型和主内核共用；搜索与键盘交互只改变当前选择，不触发远程调用。 */
export default function SelectionPopover({
  id,
  ariaLabel,
  options,
  value,
  onChange,
  placeholder = '请选择',
  disabled = false,
}: SelectionPopoverProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [menuPosition, setMenuPosition] = useState<{
    left: number; width: number; top?: number; bottom?: number
  }>({ left: 0, width: 260 })
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listId = useId()
  const selected = options.find((option) => option.value === value)
  const filtered = options.filter((option) => `${option.label} ${option.value} ${option.description ?? ''}`
    .toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const selectable = filtered.map((option, index) => option.disabled ? -1 : index).filter((index) => index !== -1)

  useEffect(() => {
    if (open && activeIndex >= 0) document.getElementById(`${listId}-${activeIndex}`)?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open, query, listId])

  useEffect(() => {
    if (!open) return
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node
        && !rootRef.current?.contains(event.target)
        && !menuRef.current?.contains(event.target)) setOpen(false)
    }
    const closeOnFocusAway = (event: FocusEvent) => {
      if (event.target instanceof Node
        && !rootRef.current?.contains(event.target)
        && !menuRef.current?.contains(event.target)) setOpen(false)
    }
    const closeOnScroll = (event: Event) => {
      // 浮层使用固定定位，父面板滚动后要关闭，避免菜单留在旧锚点。
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    document.addEventListener('focusin', closeOnFocusAway)
    document.addEventListener('scroll', closeOnScroll, true)
    window.addEventListener('resize', closeListOnResize)
    searchRef.current?.focus()
    return () => {
      document.removeEventListener('pointerdown', closeOutside)
      document.removeEventListener('focusin', closeOnFocusAway)
      document.removeEventListener('scroll', closeOnScroll, true)
      window.removeEventListener('resize', closeListOnResize)
    }
  }, [open])

  function closeListOnResize() { setOpen(false) }

  function openList() {
    if (disabled) return
    const rect = triggerRef.current?.getBoundingClientRect()
    if (rect) {
      const width = Math.min(Math.max(rect.width, 260), window.innerWidth - 16)
      const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))
      const above = window.innerHeight - rect.bottom < 290 && rect.top > window.innerHeight - rect.bottom
      setMenuPosition(above
        ? { left, width, bottom: window.innerHeight - rect.top + 6 }
        : { left, width, top: rect.bottom + 6 })
    }
    setQuery('')
    const current = options.findIndex((option) => option.value === value && !option.disabled)
    setActiveIndex(current >= 0 ? current : options.findIndex((option) => !option.disabled))
    setOpen(true)
  }

  function closeList(restoreFocus = false) {
    setOpen(false)
    setQuery('')
    if (restoreFocus) triggerRef.current?.focus()
  }

  function choose(option: SelectionOption) {
    if (option.disabled) return
    onChange(option.value)
    closeList(true)
  }

  function handleSearchKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closeList(true)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!selectable.length) return
      const current = selectable.indexOf(activeIndex)
      const direction = event.key === 'ArrowDown' ? 1 : -1
      const next = current < 0
        ? (direction === 1 ? 0 : selectable.length - 1)
        : (current + direction + selectable.length) % selectable.length
      setActiveIndex(selectable[next])
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      if (selectable.length) setActiveIndex(event.key === 'Home' ? selectable[0] : selectable.at(-1)!)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const option = filtered[activeIndex]
      if (option) choose(option)
    }
  }

  return <div className={`selection-popover${open ? ' is-open' : ''}`} ref={rootRef}>
    <button
      id={id}
      ref={triggerRef}
      type="button"
      className="selection-trigger"
      aria-label={ariaLabel}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={open ? listId : undefined}
      disabled={disabled}
      onClick={() => open ? closeList() : openList()}
      onKeyDown={(event) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault()
          openList()
        } else if (event.key === 'Escape' && open) {
          event.preventDefault()
          event.stopPropagation()
          closeList(true)
        }
      }}
    >
      <span className="selection-trigger-text">
        <span className={selected ? '' : 'selection-placeholder'}>{selected?.label ?? placeholder}</span>
        {selected?.description && <small>{selected.description}</small>}
      </span>
      <ChevronDown size={15} aria-hidden="true" />
    </button>
    {open && createPortal(<div ref={menuRef} className="selection-menu" style={menuPosition}>
      <div className="selection-search">
        <Search size={15} aria-hidden="true" />
        <input
          ref={searchRef}
          type="search"
          value={query}
          onChange={(event) => {
            const nextQuery = event.target.value
            const nextFiltered = options.filter((option) => `${option.label} ${option.value} ${option.description ?? ''}`
              .toLocaleLowerCase().includes(nextQuery.trim().toLocaleLowerCase()))
            // 筛选结果里的索引从 0 重新开始；不能沿用完整目录的索引。
            setQuery(nextQuery)
            setActiveIndex(nextFiltered.findIndex((option) => !option.disabled))
          }}
          onKeyDown={handleSearchKey}
          role="combobox"
          aria-label={`搜索${ariaLabel}`}
          aria-expanded="true"
          aria-autocomplete="list"
          aria-controls={listId}
          aria-activedescendant={filtered[activeIndex] && !filtered[activeIndex].disabled ? `${listId}-${activeIndex}` : undefined}
          placeholder="搜索名称或 ID"
        />
      </div>
      <div id={listId} role="listbox" aria-label={ariaLabel} className="selection-options">
        {filtered.length ? filtered.map((option, index) => <div
          id={`${listId}-${index}`}
          key={option.value}
          tabIndex={-1}
          role="option"
          aria-selected={option.value === value}
          aria-disabled={option.disabled || undefined}
          className={`selection-option${activeIndex === index ? ' active' : ''}${option.disabled ? ' disabled' : ''}`}
          onMouseEnter={() => { if (!option.disabled) setActiveIndex(index) }}
          onClick={() => choose(option)}
        >
          <span className="selection-option-copy"><span>{option.label}</span>{option.description && <small>{option.description}</small>}</span>
          {option.value === value && <Check size={15} aria-hidden="true" />}
        </div>) : <div className="selection-empty">没有匹配项</div>}
      </div>
    </div>, document.body)}
  </div>
}
