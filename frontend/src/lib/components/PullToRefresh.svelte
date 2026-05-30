<!--
  Pull-to-refresh for the installed (standalone) PWA.

  Standalone PWAs suppress the browser's own pull-to-refresh, so we implement the
  gesture ourselves: a downward drag that *starts at the very top of the page*
  pulls the content down (with damping); releasing past a threshold runs
  `onRefresh`. While in flight the indicator parks at a resting offset and spins.

  Listeners live on `window` because the document body is the scroll container
  (see +layout.svelte — no inner overflow). `touchmove` is registered
  non-passive so we can `preventDefault()` the native overscroll/bounce, but ONLY
  once we've committed to a downward pull at scrollTop 0 — every other gesture
  (scrolling up, scrolling the feed) is handed straight back to native scrolling.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import type { Snippet } from 'svelte';

  let {
    onRefresh,
    children
  }: {
    /** Run when the user pulls past the threshold; the indicator spins until it resolves. */
    onRefresh: () => Promise<unknown> | unknown;
    children?: Snippet;
  } = $props();

  // Gesture tuning (px / ms).
  const THRESHOLD = 72; // damped pull distance that arms a refresh
  const REST = 52; // resting offset the indicator parks at while refreshing
  const PULL_LIMIT = 150; // soft ceiling the pull eases toward — never a hard stop
  const TENSION = 1; // initial finger:content ratio (~1:1, then progressively resists)
  const DEAD_ZONE = 8; // ignore sub-threshold moves so normal scrolling isn't claimed
  const MIN_SPIN_MS = 500; // keep the spinner up at least this long so it never just blinks

  // iOS-style rubber band: content tracks the finger ~1:1 at first, then resists
  // progressively, asymptotically approaching PULL_LIMIT — so a long pull eases
  // to a soft limit instead of clamping abruptly at a fixed edge.
  function rubberBand(dy: number): number {
    return (1 - 1 / ((dy * TENSION) / PULL_LIMIT + 1)) * PULL_LIMIT;
  }

  let distance = $state(0); // current content offset
  let refreshing = $state(false); // a refresh is in flight
  let animating = $state(false); // apply the spring transition (release / settle), not while dragging

  // Plain (non-reactive) gesture bookkeeping.
  let startY = 0;
  let tracking = false; // a touch began at the top of the page
  let active = false; // committed to a pull — we now own the gesture and prevent native scroll

  const progress = $derived(Math.min(1, distance / THRESHOLD));
  const armed = $derived(distance >= THRESHOLD);

  function reset(animate = true) {
    animating = animate;
    distance = 0;
    active = false;
  }

  function onTouchStart(e: TouchEvent) {
    // Only a single-finger drag from the very top can become a pull.
    if (refreshing || e.touches.length !== 1 || window.scrollY > 0) {
      tracking = false;
      return;
    }
    startY = e.touches[0].clientY;
    tracking = true;
    active = false;
  }

  function onTouchMove(e: TouchEvent) {
    if (!tracking || refreshing) return;
    const dy = e.touches[0].clientY - startY;

    // Moving up (or scrolled off the top mid-gesture): hand back to native scrolling.
    if (dy < 1 || window.scrollY > 0) {
      tracking = false;
      if (active) reset();
      return;
    }
    // Small dead-zone before we claim the gesture, so a hair of jitter doesn't block scroll.
    if (!active && dy < DEAD_ZONE) return;

    active = true;
    animating = false; // track the finger directly, no transition lag
    distance = rubberBand(dy);
    if (e.cancelable) e.preventDefault(); // suppress native overscroll/bounce while we own it
  }

  async function onTouchEnd() {
    if (!tracking) return;
    tracking = false;
    if (!active) return;

    if (!armed || refreshing) {
      reset();
      return;
    }

    refreshing = true;
    animating = true;
    distance = REST;
    const started = Date.now();
    try {
      await onRefresh();
    } finally {
      const elapsed = Date.now() - started;
      if (elapsed < MIN_SPIN_MS) {
        await new Promise((r) => setTimeout(r, MIN_SPIN_MS - elapsed));
      }
      refreshing = false;
      reset();
    }
  }

  function onTouchCancel() {
    tracking = false;
    if (active && !refreshing) reset();
  }

  onMount(() => {
    window.addEventListener('touchstart', onTouchStart, { passive: true });
    window.addEventListener('touchmove', onTouchMove, { passive: false });
    window.addEventListener('touchend', onTouchEnd, { passive: true });
    window.addEventListener('touchcancel', onTouchCancel, { passive: true });
    return () => {
      window.removeEventListener('touchstart', onTouchStart);
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
      window.removeEventListener('touchcancel', onTouchCancel);
    };
  });
</script>

<div class="relative">
  <!-- Indicator: hidden above the fold, slides into view as the content is pulled down. -->
  <div
    class="pointer-events-none absolute inset-x-0 top-0 z-0 flex items-end justify-center"
    class:ptr-animate={animating}
    style="height:{REST}px; transform:translateY({Math.min(distance, REST) - REST}px); opacity:{refreshing
      ? 1
      : progress};"
    aria-hidden="true"
  >
    <span
      class="border-line bg-surface text-accent mb-2 grid h-8 w-8 place-items-center rounded-full border shadow-sm"
      class:animate-spin={refreshing}
    >
      <svg
        viewBox="0 0 24 24"
        class="h-4 w-4"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        style="transform:rotate({refreshing ? 0 : progress * 270}deg);"
      >
        <path d="M21 12a9 9 0 1 1-2.64-6.36" />
        <path d="M21 3v6h-6" />
      </svg>
    </span>
  </div>

  <!-- Content follows the finger, then springs back (or up to REST) via .ptr-animate. -->
  <div
    class="relative z-[1]"
    class:ptr-animate={animating}
    style="transform:translateY({distance}px);"
  >
    {@render children?.()}
  </div>

  <div class="sr-only" role="status" aria-live="polite">
    {refreshing ? 'Refreshing the feed' : armed ? 'Release to refresh' : ''}
  </div>
</div>

<style>
  .ptr-animate {
    transition: transform 0.25s cubic-bezier(0.22, 1, 0.36, 1);
  }
</style>
