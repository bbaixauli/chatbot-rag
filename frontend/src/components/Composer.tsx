import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
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

  function applySuggestion(question: string) {
    setText(question)
    const input = inputRef.current
    if (input) {
      input.focus()
      // El cursor se coloca al final tras el renderizado con el nuevo texto.
      requestAnimationFrame(() => input.setSelectionRange(question.length, question.length))
    }
  }

  return (
    <form onSubmit={submit} className="border-t border-gray-200 bg-white p-4">
      {suggestions.length > 0 && (
        <ul aria-label={t.chat.suggestions} className="mb-3 flex flex-wrap gap-2">
          {suggestions.map((question, index) => (
            <li key={`${index}-${question}`}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => applySuggestion(question)}
                className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-left text-sm text-blue-800 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50"
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
          className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={!canSend}
          className="self-end rounded-lg bg-blue-600 px-4 py-2 font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.chat.send}
        </button>
      </div>
    </form>
  )
}
