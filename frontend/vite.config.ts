import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';
// ws: true forwards the camera monitor WebSocket (/api/cameras/{id}/ws) to the backend.
// VITE_PROXY_TARGET only changes where the dev server forwards /api (default: local API).
const target = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000';
export default defineConfig({ plugins: [tailwindcss()], server: { proxy: { '/api': { target, ws: true } } } });
