import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'
import { health, jsonResponse, mockApi } from './test/utils'

describe('App', () => {
  it('monta la aplicación con cabecera, historial y chat', async () => {
    mockApi({ 'GET /health': health, 'GET /conversations': () => jsonResponse([]) })
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Chatbot RAG' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Historial de conversaciones' })).toBeVisible()
    expect(await screen.findByText('Servicio disponible')).toBeInTheDocument()
    expect(screen.getByLabelText('Escribe tu mensaje')).toBeInTheDocument()
  })
})
