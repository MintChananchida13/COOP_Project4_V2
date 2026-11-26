import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: `${process.env.BACKEND_PROXY_URL ?? "http://107.129.186.30:62100"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
