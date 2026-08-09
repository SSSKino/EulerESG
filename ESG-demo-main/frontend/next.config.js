/** @type {import('next').NextConfig} */

const nextConfig = {
  reactStrictMode: true,

  /**
   * Proxy backend routes through Next.js so the browser always talks to the same origin.
   * This avoids CORS issues and removes the need for hard-coded API base URLs.
   *
   * In docker-compose, set BACKEND_URL=http://backend:8000
   */
  async rewrites() {
    const backend = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/auth/:path*", destination: `${backend}/auth/:path*` },
      // Optional: allow direct access to persisted outputs (debug)
      { source: "/uploads/:path*", destination: `${backend}/uploads/:path*` },
    ];
  },

  // @ts-expect-error webpack config type is not fully typed
  webpack(config, { dev, isServer }) {
    if (dev && !isServer) {
      config.infrastructureLogging = {
        level: "warn",
      };
    }

    config.resolve.alias.canvas = false;
    config.resolve.fallback = {
      ...config.resolve.fallback,
      canvas: false,
    };

    return config;
  },

  devIndicators: {
    autoPrerender: false,
    position: "bottom-right",
  },
};

module.exports = nextConfig;
