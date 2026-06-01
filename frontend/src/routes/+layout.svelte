<script lang="ts">
  import '../app.css';

  let { children } = $props();

  // Dynamic copyright year (current on the client; the prerender bakes the build
  // year, hydration updates it to the visitor's current year).
  const year = new Date().getFullYear();
</script>

<div class="flex min-h-[100dvh] flex-col">
  <header class="border-b border-line bg-bg/80 backdrop-blur supports-[backdrop-filter]:bg-bg/60 sticky top-0 z-10">
    <div class="mx-auto flex max-w-2xl items-baseline gap-2 px-4 py-3 font-mono sm:px-5">
      <a href="/" class="flex items-baseline gap-2">
        <span class="text-accent text-lg leading-none font-bold">&rsaquo;</span>
        <span class="text-lg font-semibold tracking-tight">tail -f</span>
      </a>
      <span class="text-faint hidden text-xs sm:inline">news.ycombinator.com lobste.rs</span>
    </div>
  </header>

  <main class="mx-auto w-full max-w-2xl flex-1 px-4 py-5 sm:px-5">
    {@render children?.()}
  </main>

  <footer class="border-t border-line px-4 py-7 text-center text-xs sm:px-5">
    <a
      href="https://ko-fi.com/aaronmamparo"
      target="_blank"
      rel="noopener noreferrer"
      class="text-accent hover:text-accent-soft border-accent/40 hover:border-accent inline-flex items-center gap-2 rounded-full border px-4 py-2 font-medium transition-colors"
    >
      <svg
        viewBox="0 0 24 24"
        class="h-4 w-4"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M17 11h1a3 3 0 0 1 0 6h-1" />
        <path d="M9 12v6" />
        <path d="M13 12v6" />
        <path
          d="M14 7.5c-1 0-1.44.5-3 .5s-2-.5-3-.5-1.72.5-2.5.5a2.5 2.5 0 0 1 0-5c.78 0 1.57.5 2.5.5S9.44 3 11 3s2 .5 3 .5 1.72-.5 2.5-.5a2.5 2.5 0 0 1 0 5c-.78 0-1.5-.5-2.5-.5Z"
        />
        <path d="M5 8v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V8" />
      </svg>
      Buy me a beer
    </a>

    <p class="text-faint mt-5">
      © {year} · made by
      <a
        class="hover:text-accent underline-offset-2 hover:underline"
        href="https://aaronmamparo.com"
        target="_blank"
        rel="noopener noreferrer">Aaron Mamparo</a
      >
    </p>
  </footer>
</div>
