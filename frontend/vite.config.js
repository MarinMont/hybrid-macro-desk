import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 単一ページ。バックエンド (localhost:8787) はフロントが実行時に自動検出するため
// プロキシは不要 (CORSはバックエンド側で全許可)。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
