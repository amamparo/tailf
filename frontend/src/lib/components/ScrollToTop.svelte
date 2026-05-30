<!--
  "Back to top" pill. Appears once the page is scrolled more than `threshold` px
  from the top, anchored just below the sticky header. Clicking it does a fast,
  distance-independent animated scroll to the top, then runs `onReachTop` (the
  page wires this to a soft data reload).

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
    transition:fly={{ y: -8, duration: 160 }}
    aria-label="Back to top"
    class="border-line bg-surface/90 text-muted hover:border-accent/50 hover:text-accent fixed top-14 left-1/2 z-20 flex -translate-x-1/2 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs font-medium shadow-lg backdrop-blur transition-colors"
  >
    <svg
      viewBox="0 0 24 24"
      class="h-3.5 w-3.5"
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
    Back to top
  </button>
{/if}
