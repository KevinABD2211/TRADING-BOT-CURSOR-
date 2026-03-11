/** @type {import('next').NextConfig} */
const apiUrl = process.env.NEXT_PUBLIC_API_URL || '';

const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  // When deployed, proxy /api/* to the backend so opening /api/advice etc. doesn't 404
  async rewrites() {
    if (apiUrl && !apiUrl.includes('localhost')) {
      return [{ source: '/api/:path*', destination: `${apiUrl.replace(/\/$/, '')}/api/:path*` }];
    }
    return [];
  },
};

module.exports = nextConfig;
