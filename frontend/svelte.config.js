import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // Fully static output. `fallback: 'index.html'` makes the build an SPA shell:
    // the page prerenders, but the actual data is fetched client-side from
    // same-origin /data.json at runtime (NetworkFirst via the service worker).
    adapter: adapter({
      fallback: 'index.html',
      precompress: false,
      strict: true
    })
  }
};

export default config;
