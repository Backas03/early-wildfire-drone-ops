import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 개발 서버(5173)에서 /api, /ws, /media(탐지 사진) 요청을 대시보드 API(8000)로 넘긴다
const API = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': API,
      '/media': API,
      '/ws': { target: API, ws: true },
    },
  },
})
