import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const API_TARGET = env.VITE_API_TARGET || 'https://gis-hq.tail3b9b19.ts.net'

  return {
    plugins: [react()],
    server: {
      host: env.VITE_DEV_HOST || '0.0.0.0',
      port: Number(env.VITE_DEV_PORT) || 3000,
      strictPort: true,
      proxy: {
        '/vworld': {
          target: 'https://api.vworld.kr',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/vworld/, ''),
        },
        '/api': {
          target: API_TARGET,
          changeOrigin: true,
          secure: false,
        },
        '/s3': {
          target: API_TARGET,
          changeOrigin: true,
          secure: false,
        },
      },
    },
  }
})
