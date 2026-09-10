import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API is proxied so the browser sees one origin during development and
    // the session cookie behaves as it will in production. Pointing the client
    // straight at :8000 would work until the first cookie, and then stop.
    //
    // Note that uploads deliberately do NOT go through here: they are PUT
    // straight to object storage with a presigned URL, which is the whole
    // reason PDF bytes never reach the API tier.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
});
