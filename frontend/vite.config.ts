import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API base URL is read from VITE_API_URL at build/dev time
// (defaults to the local FastAPI server).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
