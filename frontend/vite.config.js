import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Data Agent Studio — frontend scaffold.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: true,
  },
})
