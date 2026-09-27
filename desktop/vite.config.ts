import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发窗口和浏览器共用同一前端；只有 /api 会转发给本机 Python 服务。
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
      },
    },
  },
})
