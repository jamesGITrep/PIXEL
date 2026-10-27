import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        // Hash-only names for code-split chunks: the main bundle should not advertise what the
        // lazily loaded pages are (the unlisted admin console is one of them).
        chunkFileNames: 'assets/[hash].js',
      },
    },
  }
});
