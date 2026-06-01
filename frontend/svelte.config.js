import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // Fully static output. Every route is prerendered to its own HTML file
    // (`/` -> index.html, `/privacy` -> privacy.html) so each ships full SSR
    // head + markup (h1, per-page canonical/title, footer). NO SPA fallback:
    // a fallback would clobber the prerendered `/` with an empty shell, and we
    // no longer need client-side catch-all routing — unknown paths return a
    // real 404 at the edge (CloudFront), and the home page still fetches
    // /data.json client-side on mount (NetworkFirst via the service worker).
    adapter: adapter({
      precompress: false,
      strict: true
    })
  }
};

export default config;
