import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { NavLink, useNavigate, useParams } from 'react-router'
import {
  deleteConversation,
  listConversations,
  renameConversation,
  type Conversation,
} from '../api/client'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { t } from '../i18n/es'

export const SIDEBAR_ID = 'historial'

/**
 * Historial de conversaciones. En pantallas de menos de 768 px se pliega tras el botón
 * «Menú» de la cabecera (`open`); `onNavigate` lo cierra al elegir una opción.
 */
export function Sidebar({ open = false, onNavigate }: { open?: boolean; onNavigate?: () => void }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { conversationId } = useParams()
  const [editingId, setEditingId] = useState<string | null>(null)

  const conversations = useQuery({ queryKey: ['conversations'], queryFn: listConversations })

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => renameConversation(id, title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['conversations'] }),
  })

  const remove = useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: (_, id) => {
      queryClient.removeQueries({ queryKey: ['messages', id] })
      if (id === conversationId) navigate('/')
      return queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  function confirmDelete(conversation: Conversation) {
    if (window.confirm(t.sidebar.confirmDelete(conversation.title))) {
      remove.mutate(conversation.id)
    }
  }

  return (
    <nav
      id={SIDEBAR_ID}
      aria-label={t.sidebar.label}
      className={`${open ? 'flex' : 'hidden'} absolute inset-y-0 left-0 z-20 w-full max-w-xs flex-col border-r border-borde bg-fondo shadow-lg md:static md:flex md:w-72 md:max-w-none md:shrink-0 md:shadow-none`}
    >
      <div className="p-3">
        <button
          type="button"
          onClick={() => {
            onNavigate?.()
            navigate('/')
          }}
          className="btn btn-secundario w-full"
        >
          <Plus aria-hidden size={18} />
          {t.sidebar.newChat}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {conversations.isError && (
          <p role="alert" className="px-2 text-sm text-error">
            {t.sidebar.loadError}
          </p>
        )}
        {conversations.data?.length === 0 && (
          <p className="px-2 text-sm text-texto-secundario">{t.sidebar.empty}</p>
        )}
        <ul className="flex flex-col gap-1">
          {conversations.data?.map((conversation) => (
            <li key={conversation.id} className="flex items-center gap-1">
              {editingId === conversation.id ? (
                <RenameInput
                  initial={conversation.title}
                  onCancel={() => setEditingId(null)}
                  onSave={(title) => {
                    setEditingId(null)
                    if (title !== conversation.title) rename.mutate({ id: conversation.id, title })
                  }}
                />
              ) : (
                <>
                  <NavLink
                    to={`/c/${conversation.id}`}
                    onClick={onNavigate}
                    className="nav-item min-w-0 flex-1 text-sm"
                  >
                    <span className="truncate">{conversation.title}</span>
                  </NavLink>
                  <IconButton
                    label={`${t.sidebar.rename}: ${conversation.title}`}
                    onClick={() => setEditingId(conversation.id)}
                  >
                    <Pencil aria-hidden size={16} />
                  </IconButton>
                  <IconButton
                    label={`${t.sidebar.delete}: ${conversation.title}`}
                    onClick={() => confirmDelete(conversation)}
                  >
                    <Trash2 aria-hidden size={16} />
                  </IconButton>
                </>
              )}
            </li>
          ))}
        </ul>
      </div>
    </nav>
  )
}

function IconButton(props: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={props.label}
      title={props.label}
      onClick={props.onClick}
      className="btn btn-icono shrink-0"
    >
      {props.children}
    </button>
  )
}

function RenameInput(props: {
  initial: string
  onSave: (title: string) => void
  onCancel: () => void
}) {
  const [value, setValue] = useState(props.initial)
  const save = () => (value.trim() ? props.onSave(value.trim()) : props.onCancel())
  return (
    <input
      aria-label={t.sidebar.renameInput}
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === 'Enter') save()
        if (e.key === 'Escape') props.onCancel()
      }}
      className="campo min-w-0 flex-1 text-sm"
    />
  )
}
