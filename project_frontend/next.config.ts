import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: "http://107.129.186.30:62100/:path*",
      },
    ];
  },
};

export default nextConfig;