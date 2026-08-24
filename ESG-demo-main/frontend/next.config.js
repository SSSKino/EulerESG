/** @type {import('next').NextConfig} */

const showDevTools = /^(1|true|yes|on)$/i.test(
  (process.env.NEXT_PUBLIC_SHOW_DEV_TOOLS || "").trim(),
);

const nextConfig = {
  reactStrictMode: true,

  // Keep recently visited route bundles warm in development. The application
  // has several large workspaces; letting Next evict them quickly makes a
  // return navigation look like a frozen click while the route recompiles.
  onDemandEntries: {
    maxInactiveAge: 30 * 60 * 1000,
    pagesBufferLength: 12,
  },

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

  devIndicators: showDevTools ? { position: "bottom-right" } : false,
};

module.exports = nextConfig;
