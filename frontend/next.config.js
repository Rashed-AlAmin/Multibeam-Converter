/** @type {import('next').NextConfig} */

// The browser talks to the Next server only; Next forwards /api to the
// converter. One origin means no CORS handling and no hard-coded host in the
// client bundle. Set NEXT_PUBLIC_API_BASE to bypass the proxy if you would
// rather the browser hit the backend directly.
const BACKEND_URL = process.env.BACKEND_URL || "http://backend:8000";

module.exports = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};
