import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const { WEB_GATEWAY_API_KEY = "" } = loadEnv(mode, "..", "");
  return {
    plugins: [react()],
    server: {
      port: 8080,
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
          configure(proxy) {
            proxy.on("proxyReq", (proxyRequest) => {
              // Match production Nginx: never trust a browser-supplied identity.
              proxyRequest.removeHeader("Authorization");
              if (WEB_GATEWAY_API_KEY) {
                proxyRequest.setHeader("Authorization", `Bearer ${WEB_GATEWAY_API_KEY}`);
              }
            });
          },
        },
      },
    },
  };
});
