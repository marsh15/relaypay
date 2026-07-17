import type { NextConfig } from "next";

const developmentSources = process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "";
const developmentConnections = process.env.NODE_ENV === "development" ? " ws:" : "";
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  {
    key: "Content-Security-Policy",
    value:
      `default-src 'self'; script-src 'self' 'unsafe-inline'${developmentSources}; ` +
      "style-src 'self' 'unsafe-inline'; img-src 'self' data:; " +
      `connect-src 'self'${developmentConnections}; font-src 'self'; frame-ancestors 'none'; ` +
      "base-uri 'self'; form-action 'self'",
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  allowedDevOrigins: ["127.0.0.1"],
  turbopack: { root: process.cwd() },
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default nextConfig;
