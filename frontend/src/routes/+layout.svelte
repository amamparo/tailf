<script lang="ts">
  import '../app.css';

  let { children } = $props();

  // Dynamic copyright year (current on the client; the prerender bakes the build
  // year, hydration updates it to the visitor's current year).
  const year = new Date().getFullYear();
</script>

<div class="flex min-h-[100dvh] flex-col">
  <a href="#main-content" class="skip-link">Skip to content</a>
  <header class="border-b border-line bg-bg/80 backdrop-blur supports-[backdrop-filter]:bg-bg/60 sticky top-0 z-10">
    <div class="mx-auto flex max-w-2xl items-center gap-2 px-4 py-3 font-mono text-lg sm:px-5 sm:text-sm">
      <h1 class="flex">
        <a href="/" class="flex items-center gap-2">
          <span class="text-accent text-2xl leading-none font-bold sm:text-lg">&rsaquo;</span>
          <span class="font-semibold tracking-tight">tail -f</span>
        </a>
      </h1>
      <!-- The tailed sources: look identical to plain faint text at rest (no
           underline, same color); only a hover color hints they're clickable. -->
      <span class="text-faint hidden font-normal sm:inline"
        ><a
          href="https://news.ycombinator.com"
          target="_blank"
          rel="noopener noreferrer"
          class="transition-colors hover:text-accent">news.ycombinator.com</a
        >
        <a
          href="https://lobste.rs"
          target="_blank"
          rel="noopener noreferrer"
          class="transition-colors hover:text-accent">lobste.rs</a
        ></span
      >
    </div>
  </header>

  <main id="main-content" tabindex="-1" class="mx-auto w-full max-w-2xl flex-1 px-4 py-5 focus:outline-none sm:px-5">
    {@render children?.()}
  </main>

  <footer class="border-t border-line px-4 py-7 text-center text-xs sm:px-5">
    <p class="text-faint">
      © {year} · by
      <a
        class="underline decoration-faint underline-offset-2 transition-colors hover:text-accent hover:decoration-accent"
        href="https://aaronmamparo.com"
        target="_blank"
        rel="noopener noreferrer">Aaron Mamparo</a
      >
      ·
      <a
        class="underline decoration-faint underline-offset-2 transition-colors hover:text-accent hover:decoration-accent"
        href="/privacy">Privacy</a
      >
      ·
      <a
        class="underline decoration-faint underline-offset-2 transition-colors hover:text-accent hover:decoration-accent"
        href="https://ko-fi.com/aaronmamparo"
        target="_blank"
        rel="noopener noreferrer">Buy me a beer</a
      >
    </p>
  </footer>
</div>
