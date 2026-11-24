// Nama aplikasi belum final: selalu ambil dari konfigurasi, jangan di-hardcode (PRD header).
export const APP_NAME: string = import.meta.env.VITE_APP_NAME || "[APP_NAME]";
