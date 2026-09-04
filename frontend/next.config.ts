import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone with a self-contained server.js, so the runtime
  // image ships only the files actually needed instead of the full
  // node_modules tree. See frontend/Dockerfile - the standalone server does
  // NOT bundle `public/` or `.next/static/`, so those are copied in
  // explicitly there.
  output: "standalone",
};

export default nextConfig;
