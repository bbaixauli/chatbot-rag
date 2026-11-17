import { HealthBadge } from './components/HealthBadge'
import { t } from './i18n/es'

export default function App() {
  return (
    <div className="flex h-full flex-col bg-gray-50 text-gray-900">
      <header className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
        <h1 className="text-lg font-semibold">{t.appName}</h1>
        <HealthBadge />
      </header>
      <main className="flex flex-1 flex-col items-center justify-center gap-2 p-6">
        <h2 className="text-2xl font-semibold">{t.home.welcome}</h2>
        <p className="text-gray-600">{t.home.placeholder}</p>
      </main>
    </div>
  )
}
