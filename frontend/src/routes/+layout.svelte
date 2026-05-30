<script lang="ts">
  import '../app.css';

  let { children } = $props();
</script>

<div class="flex min-h-[100dvh] flex-col">
  <header class="border-b border-line bg-bg/80 backdrop-blur supports-[backdrop-filter]:bg-bg/60 sticky top-0 z-10">
    <div class="mx-auto flex max-w-2xl items-baseline gap-2 px-4 py-3 sm:px-5">
      <a href="/" class="flex items-baseline gap-2">
        <span class="text-accent font-mono text-lg leading-none font-bold">&rsaquo;</span>
        <span class="text-lg font-semibold tracking-tight">hackergist</span>
      </a>
      <span class="text-faint hidden text-xs sm:inline">the gist of Hacker News</span>
    </div>
  </header>

  <main class="mx-auto w-full max-w-2xl flex-1 px-4 py-5 sm:px-5">
    {@render children?.()}
  </main>

  <footer class="text-faint border-t border-line px-4 py-5 text-center text-xs sm:px-5">
    <p>
      AI gists of the linked articles. Stories &amp; points from
      <a class="hover:text-accent underline-offset-2 hover:underline" href="https://news.ycombinator.com" target="_blank" rel="noopener noreferrer">Hacker News</a>.
    </p>
  </footer>
</div>
