# packages/ — kode TypeScript bersama (web & mobile)

Direncanakan (PRD §6.1), dibuat saat dibutuhkan:

| Paket | Isi | Fase |
|---|---|---|
| `api-client` | Client API di-generate dari OpenAPI backend (tidak ditulis tangan) | 1 |
| `ui` | Design system: token, komponen, Storybook | 1 |
| `i18n` | Kunci terjemahan id & en | 1 |
| `sync` | Engine sinkronisasi offline (outbox, pull/push) | 3 |

Setiap paket adalah workspace pnpm (`packages/<nama>/package.json`).
