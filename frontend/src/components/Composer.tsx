import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { t } from '../i18n/es'

interface Props {
  disabled: boolean
  onSend: (text: string) => void
}

export function Composer({ disabled, onSend }: Props) {
  const [text, setText] = useState('')
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

  return (
    <form onSubmit={submit} className="flex gap-2 border-t border-gray-200 bg-white p-4">
      <textarea
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
    </form>
  )
}
