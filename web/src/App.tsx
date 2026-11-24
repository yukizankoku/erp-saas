import { APP_NAME } from "./config";

export function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="flex h-14 items-center border-b border-slate-200 bg-white px-4">
        <span className="font-semibold">{APP_NAME}</span>
      </header>
      <main className="p-6">
        <h1 className="text-xl font-semibold">Selamat datang di {APP_NAME}</h1>
        <p className="mt-2 text-sm text-slate-600">
          Kerangka aplikasi (Fase 0). Modul akan muncul di sini mulai Fase 1.
        </p>
      </main>
    </div>
  );
}
