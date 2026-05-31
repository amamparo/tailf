<!--
  "Back to top" floating button — a circular FAB fixed in the bottom-right
  (the universal scroll-to-top convention: thumb-reachable, out of the way of
  content). Appears once the page is scrolled more than `threshold` px from the
  top. Clicking it does a fast, distance-independent animated scroll to the top,
  then runs `onReachTop` (the page wires this to a soft data reload). Inset from
  the edges + the PWA safe-area so it clears the home indicator in standalone.

  Must be rendered OUTSIDE PullToRefresh: a `transform` on an ancestor would
  re-anchor this `position: fixed` button to that ancestor instead of the
  viewport. Keep it a top-level sibling in +page.svelte.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { fly } from 'svelte/transition';

  let {
    onReachTop,
    threshold = 500
  }: {
    /** Called once the animated scroll reaches the top. */
    onReachTop?: () => void;
    /** Show the button after scrolling this many px from the top. */
    threshold?: number;
  } = $props();

  const SCROLL_MS = 320; // fast, regardless of how far down the user is

  let visible = $state(false);

  function onScroll() {
    visible = window.scrollY > threshold;
  }

  function scrollToTop() {
    const start = window.scrollY;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduce || start <= 0) {
      window.scrollTo(0, 0);
      onReachTop?.();
      return;
    }
    const t0 = performance.now();
    function step(t: number) {
      const p = Math.min(1, (t - t0) / SCROLL_MS);
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      window.scrollTo(0, Math.round(start * (1 - eased)));
      if (p < 1) {
        requestAnimationFrame(step);
      } else {
        onReachTop?.();
      }
    }
    requestAnimationFrame(step);
  }

  onMount(() => {
    onScroll(); // set initial state (e.g. reload mid-page)
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  });
</script>

{#if visible}
  <button
    type="button"
    onclick={scrollToTop}
    transition:fly={{ y: 8, duration: 160 }}
    aria-label="Back to top"
    title="Back to top"
    class="border-line bg-surface/90 text-muted hover:border-accent/50 hover:text-accent fixed right-4 z-20 grid h-11 w-11 place-items-center rounded-full border shadow-lg backdrop-blur transition-colors sm:right-5"
    style="bottom: calc(1.25rem + env(safe-area-inset-bottom, 0px));"
  >
    <svg
      viewBox="0 0 24 24"
      class="h-5 w-5"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M12 19V5" />
      <path d="M5 12l7-7 7 7" />
    </svg>
  </button>
{/if}
