import { render, screen, within } from '@testing-library/react'
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

  it('al pulsar una pregunta sugerida sustituye el texto y permite editarla antes de enviar', async () => {
    const onSend = vi.fn()
    render(
      <Composer
        disabled={false}
        onSend={onSend}
        suggestions={['¿Cuántos días de vacaciones tengo?', 'Otra']}
      />,
    )
    const input = screen.getByLabelText('Escribe tu mensaje')
    const list = screen.getByRole('list', { name: 'Preguntas sugeridas' })
    await userEvent.type(input, 'texto previo')

    await userEvent.click(
      within(list).getByRole('button', { name: '¿Cuántos días de vacaciones tengo?' }),
    )

    expect(input).toHaveValue('¿Cuántos días de vacaciones tengo?')
    expect(input).toHaveFocus()
    expect(onSend).not.toHaveBeenCalled()

    await userEvent.click(within(list).getByRole('button', { name: 'Otra' }))
    expect(input).toHaveValue('Otra')
    await userEvent.type(input, ' más{Enter}')
    expect(onSend).toHaveBeenCalledWith('Otra más')
  })

  it('sin sugerencias no muestra la lista y deshabilita las sugerencias si está ocupado', () => {
    const { rerender } = render(<Composer disabled={false} onSend={vi.fn()} />)
    expect(screen.queryByRole('list', { name: 'Preguntas sugeridas' })).toBeNull()

    rerender(<Composer disabled onSend={vi.fn()} suggestions={['Una']} />)
    expect(screen.getByRole('button', { name: 'Una' })).toBeDisabled()
  })
})
