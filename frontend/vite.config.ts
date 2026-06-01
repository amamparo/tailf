import { sveltekit } from '@sveltejs/kit/vite';
import { SvelteKitPWA } from '@vite-pwa/sveltekit';
import tailwindcss from '@tailwindcss/vite';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import type { Plugin } from 'vite';
import { defineConfig } from 'vite';

const here = dirname(fileURLToPath(import.meta.url));

/**
 * Dev-only middleware that mirrors the production same-origin fetch of /data.json.
 *
 * In prod, CloudFront serves data.json from S3 next to the static site, so the
 * SPA's `fetch('/data.json')` resolves same-origin. Locally there is no S3, so we
 * serve the pipeline's output from `../.data/data.json` (written by `just index`),
 * falling back to the bundled sample at `./static/data.json` if it doesn't exist.
 */
function serveLocalData(): Plugin {
  const dataPath = resolve(here, '..', '.data', 'data.json');
  // Dev-only fallback sample. Deliberately NOT under static/ — anything in
  // static/ is served by SvelteKit at /data.json (shadowing this middleware and
  // the real .data/data.json) AND copied into build/ where the CDK
  // BucketDeployment would push it to S3, clobbering the Lambda's data.json.
  const samplePath = resolve(here, 'data.sample.json');

  return {
    name: 'tailf:serve-local-data',
    apply: 'serve',
    // Run before SvelteKit's own middlewares so /data.json is intercepted here.
    enforce: 'pre',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = req.url?.split('?')[0];
        if (req.method !== 'GET' || url !== '/data.json') {
          next();
          return;
        }
        let body: Buffer | null = null;
        let source = '.data';
        try {
          body = await readFile(dataPath);
        } catch {
          try {
            body = await readFile(samplePath);
            source = 'sample';
          } catch {
            body = null;
          }
        }
        if (body === null) {
          res.statusCode = 404;
          res.setHeader('Content-Type', 'application/json');
          res.end(
            JSON.stringify({
              error: 'no data.json found. Run `just index` to populate .data/data.json.'
            })
          );
          return;
        }
        res.statusCode = 200;
        res.setHeader('Content-Type', 'application/json; charset=utf-8');
        res.setHeader('Cache-Control', 'no-store');
        res.setHeader('X-Tailf-Data-Source', source);
        res.end(body);
      });
    }
  };
}

export default defineConfig({
  plugins: [
    tailwindcss(),
    sveltekit(),
    serveLocalData(),
    SvelteKitPWA({
      registerType: 'autoUpdate',
      // SvelteKit emits a SPA fallback (index.html); cache it as the offline app shell.
      strategies: 'generateSW',
      manifest: {
        name: 'tail -f',
        short_name: 'tail -f',
        description:
          'AI gists of the top stories on Hacker News and lobste.rs, in one live feed.',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        theme_color: '#0b0f14',
        background_color: '#0b0f14',
        categories: ['news', 'productivity'],
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          {
            src: '/icons/icon-512-maskable.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable'
          }
        ]
      },
      pwaAssets: { disabled: true },
      workbox: {
        globPatterns: ['client/**/*.{js,css,ico,png,svg,webp,woff,woff2}', 'prerendered/**/*.html'],
        navigateFallback: '/',
        runtimeCaching: [
          {
            // The data layer: fresh when online, served from cache when offline.
            urlPattern: ({ url }) => url.pathname === '/data.json',
            handler: 'NetworkFirst',
            options: {
              // v2: bumped from 'tailf-data' so the schema-v2 deploy can't
              // serve a stale cached v1 body to an offline/installed PWA.
              cacheName: 'tailf-data-v2',
              networkTimeoutSeconds: 5,
              // Keep the LAST successfully-fetched feed for a long time so a PWA
              // reopened offline still shows real cards instead of an error.
              // NetworkFirst always refreshes when online; this max-age only
              // bounds how long the offline fallback copy survives un-refreshed.
              expiration: { maxEntries: 2, maxAgeSeconds: 60 * 60 * 24 * 30 },
              cacheableResponse: { statuses: [0, 200] }
            }
          }
        ]
      },
      devOptions: {
        enabled: false,
        type: 'module',
        navigateFallback: '/'
      }
    })
  ]
});
