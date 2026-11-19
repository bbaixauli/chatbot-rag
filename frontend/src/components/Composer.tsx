import { useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { t } from '../i18n/es'

interface Props {
  disabled: boolean
  onSend: (text: string) => void
  /** Preguntas que, al pulsarlas, sustituyen el texto del campo (editable antes de enviar). */
  suggestions?: string[]
}

export function Composer({ disabled, onSend, suggestions = [] }: Props) {
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const focusAtEnd = useRef(false)
  const canSend = !disabled && text.trim().length > 0

  function submit(event?: FormEvent) {
    event?.preventDefault()
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  // Tras aplicar una sugerencia, se enfoca el campo con el cursor al final. Se hace en el
  // mismo commit del renderizado para no interferir con lo que el usuario teclee después.
  useLayoutEffect(() => {
    if (!focusAtEnd.current) return
    focusAtEnd.current = false
    focusInputAtEnd()
  })

  function focusInputAtEnd() {
    const input = inputRef.current
    if (!input) return
    input.focus()
    input.setSelectionRange(input.value.length, input.value.length)
  }

  function applySuggestion(question: string) {
    // Si el texto no cambia, React no vuelve a renderizar: se enfoca directamente.
    if (question === text) return focusInputAtEnd()
    focusAtEnd.current = true
    setText(question)
  }

  return (
    <form onSubmit={submit} className="border-t border-borde bg-fondo p-3 md:p-4">
      {suggestions.length > 0 && (
        <ul aria-label={t.chat.suggestions} className="mb-3 flex flex-wrap gap-2">
          {suggestions.map((question, index) => (
            <li key={`${index}-${question}`}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => applySuggestion(question)}
                className="btn btn-secundario text-left font-normal"
              >
                {question}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <textarea
          ref={inputRef}
          aria-label={t.chat.inputLabel}
          placeholder={t.chat.inputPlaceholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          className="campo min-w-0 flex-1 resize-none"
        />
        <button type="submit" disabled={!canSend} className="btn btn-primario self-end">
          {t.chat.send}
        </button>
      </div>
      <p className="mt-2 text-xs text-texto-secundario">{t.chat.aiNotice}</p>
    </form>
  )
}
