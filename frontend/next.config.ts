import type { NextConfig } from "next";

const backendUrl = process.env.NEWS_PILOT_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "export",
  ...(process.env.NODE_ENV === "development"
    ? {
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: `${backendUrl}/:path*`,
            },
          ];
        },
      }
    : {}),
};

export default nextConfig;
