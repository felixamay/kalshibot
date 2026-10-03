import type { NextConfig } from "next";

const isFirebaseHosting = process.env.FIREBASE_HOSTING === "1";

const nextConfig: NextConfig = {
  output: isFirebaseHosting ? "export" : "standalone",
  images: isFirebaseHosting ? { unoptimized: true } : undefined,
  trailingSlash: isFirebaseHosting ? true : undefined,
};

export default nextConfig;
