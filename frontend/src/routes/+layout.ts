// Prerender the app shell at build time (adapter-static). The page itself still
// fetches /data.json client-side on mount, so data stays fresh per request.
export const prerender = true;
export const ssr = true;
export const trailingSlash = 'ignore';
