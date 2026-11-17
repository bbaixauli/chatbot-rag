import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Composer } from './Composer'

describe('Composer', () => {
  it('envía con Intro, recorta espacios y limpia el campo', async () => {
    const onSend = vi.fn()
    render(<Composer disabled={false} onSend={onSend} />)
    const input = screen.getByLabelText('Escribe tu mensaje')

    await userEvent.type(input, '  hola  {Enter}')

    expect(onSend).toHaveBeenCalledWith('hola')
    expect(input).toHaveValue('')
  })

  it('Mayús+Intro inserta una nueva línea', async () => {
    const onSend = vi.fn()
    render(<Composer disabled={false} onSend={onSend} />)
    const input = screen.getByLabelText('Escribe tu mensaje')

    await userEvent.type(input, 'a{Shift>}{Enter}{/Shift}b')

    expect(onSend).not.toHaveBeenCalled()
    expect(input).toHaveValue('a\nb')
  })

  it('no envía texto vacío ni estando deshabilitado', async () => {
    const onSend = vi.fn()
    const { rerender } = render(<Composer disabled={false} onSend={onSend} />)
    const button = screen.getByRole('button', { name: 'Enviar' })
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Escribe tu mensaje'), '   {Enter}')

    rerender(<Composer disabled onSend={onSend} />)
    await userEvent.type(screen.getByLabelText('Escribe tu mensaje'), 'hola{Enter}')

    expect(onSend).not.toHaveBeenCalled()
    expect(button).toBeDisabled()
  })
})
