import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { NavLink, useNavigate, useParams } from 'react-router'
import {
  deleteConversation,
  listConversations,
  renameConversation,
  type Conversation,
} from '../api/client'
import { t } from '../i18n/es'

export function Sidebar() {
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
      aria-label={t.sidebar.label}
      className="flex w-72 shrink-0 flex-col border-r border-gray-200 bg-white"
    >
      <div className="p-3">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm font-medium hover:bg-gray-50"
        >
          + {t.sidebar.newChat}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {conversations.isError && (
          <p role="alert" className="px-2 text-sm text-red-700">
            {t.sidebar.loadError}
          </p>
        )}
        {conversations.data?.length === 0 && (
          <p className="px-2 text-sm text-gray-500">{t.sidebar.empty}</p>
        )}
        <ul className="flex flex-col gap-1">
          {conversations.data?.map((conversation) => (
            <li key={conversation.id} className="group flex items-center gap-1">
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
                    className={({ isActive }) =>
                      `flex-1 truncate rounded-md px-2 py-1.5 text-sm ${
                        isActive ? 'bg-gray-100 font-medium' : 'hover:bg-gray-50'
                      }`
                    }
                  >
                    {conversation.title}
                  </NavLink>
                  <IconButton
                    label={`${t.sidebar.rename}: ${conversation.title}`}
                    onClick={() => setEditingId(conversation.id)}
                  >
                    ✎
                  </IconButton>
                  <IconButton
                    label={`${t.sidebar.delete}: ${conversation.title}`}
                    onClick={() => confirmDelete(conversation)}
                  >
                    🗑
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

function IconButton(props: { label: string; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-label={props.label}
      title={props.label}
      onClick={props.onClick}
      className="rounded px-1 text-gray-400 opacity-0 group-hover:opacity-100 hover:text-gray-700 focus:opacity-100"
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
      className="flex-1 rounded-md border border-blue-500 px-2 py-1 text-sm focus:outline-none"
    />
  )
}
