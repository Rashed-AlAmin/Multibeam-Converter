/** @type {import('next').NextConfig} */

// The browser talks to the Next server only; Next forwards /api to the
// converter. One origin means no CORS handling and no backend hostname
// compiled into the client bundle.
//
// Next caps a proxied request body at 10 MB by default, which silently
// truncates a survey upload and drops the connection. Survey files are
// routinely hundreds of megabytes, so the cap is raised to match the
// backend's own MAX_UPLOAD_BYTES.
const BACKEND_URL = process.env.BACKEND_URL || "http://backend:8000";
const MAX_BODY = process.env.MAX_UPLOAD_BYTES || "2147483648"; // 2 GiB

module.exports = {
  output: "standalone",
  experimental: {
    middlewareClientMaxBodySize: Number(MAX_BODY),
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};
