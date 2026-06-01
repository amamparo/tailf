<script lang="ts">
  import { onMount } from 'svelte';
  import { loadData, DataError } from '$lib/data';
  import { sortByHotness } from '$lib/hotness';
  import { relativeTime } from '$lib/time';
  import type { DataFile, Story } from '$lib/types';
  import StoryCard from '$lib/components/StoryCard.svelte';
  import PullToRefresh from '$lib/components/PullToRefresh.svelte';
  import ScrollToTop from '$lib/components/ScrollToTop.svelte';

  let data = $state<DataFile | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // A ticking clock so the hotness ordering (and "x ago" labels) decay live
  // between the backend's hourly refreshes, without re-fetching.
  let now = $state(Date.now());

  // When the installed PWA is reopened after sitting in the background, refetch
  // if the data we're showing is older than this. The backend writes hourly, so
  // anything past a minute is worth revalidating; the threshold just stops a
  // quick app-switch from refetching needlessly. (NetworkFirst makes the fetch
  // cheap — fresh online, cached offline.)
  const STALE_AFTER_MS = 60_000;
  let lastLoadedAt = 0;
  let inFlight = false; // coalesce overlapping loads (initial / foreground / pull)

  const stories = $derived<Story[]>(data?.stories ?? []);

  // One feed: stories WITH a gist, sorted by client-side hotness. Gist-less
  // stories (unreadable sources) are hidden — the backend keeps retrying them on
  // later runs, and they appear once a gist lands. No top-N cap, no feed split.
  const visible = $derived.by(() => sortByHotness(stories.filter((s) => s.gist), now));

  const updatedLabel = $derived(data ? relativeTime(data.generated_at, now) : '');

  // End-of-feed status line: how many posts are shown + when the feed was last
  // written, attributed to whichever sources are actually present.
  const postWord = $derived(visible.length === 1 ? 'post' : 'posts');

  const SOURCE_LABELS: Record<string, string> = { hn: 'Hacker News', lobsters: 'lobste.rs' };
  const sourcesLabel = $derived.by(() => {
    const present = new Set<string>();
    for (const s of visible) for (const d of s.discussions) present.add(d.source);
    const labels = [...present].map((s) => SOURCE_LABELS[s] ?? s);
    return labels.length ? labels.join(' + ') : 'the feeds';
  });

  // A `soft` load refreshes in place: no loading skeleton — used by the
  // foreground refetch, pull-to-refresh, and the back-to-top button. A hard load
  // (initial mount, Retry button) shows the skeleton. Either way, a *failed*
  // fetch never wipes cards that are already on screen (see catch below).
  async function load({ soft = false }: { soft?: boolean } = {}) {
    if (inFlight) return;
    inFlight = true;
    if (!soft) {
      loading = true;
      error = null;
    }
    try {
      data = await loadData();
      error = null;
      now = Date.now();
      lastLoadedAt = Date.now();
    } catch (err) {
      const message =
        err instanceof DataError
          ? err.message
          : 'Something went wrong loading the gist feed.';
      // A failed fetch (>=4xx, network drop, bad JSON) must NEVER replace cards
      // that are already on screen — keep what we have. We only surface the error
      // state when there's nothing to show yet (e.g. the initial load failed).
      if (data) {
        console.warn('[tailf] feed refresh failed; keeping current cards:', message);
      } else {
        error = message;
        data = null;
      }
    } finally {
      if (!soft) loading = false;
      inFlight = false;
    }
  }

  // Reopening the installed app, or returning to the tab, refetches if stale.
  function refreshIfStale() {
    if (document.visibilityState !== 'visible') return;
    if (Date.now() - lastLoadedAt < STALE_AFTER_MS) return;
    void load({ soft: true });
  }

  onMount(() => {
    void load();
    const id = setInterval(() => (now = Date.now()), 60_000);
    document.addEventListener('visibilitychange', refreshIfStale);
    // pageshow with persisted=true fires when the page is restored from bfcache.
    window.addEventListener('pageshow', refreshIfStale);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', refreshIfStale);
      window.removeEventListener('pageshow', refreshIfStale);
    };
  });
</script>

<svelte:head>
  <title>tail -f news.ycombinator.com lobste.rs</title>
</svelte:head>

<ScrollToTop onReachTop={() => load({ soft: true })} />

<PullToRefresh onRefresh={() => load({ soft: true })}>
  <section class="flex flex-col gap-4">
    {#if loading}
      <ul class="flex flex-col gap-3" aria-busy="true" aria-label="Loading stories">
        {#each [0, 1, 2, 3, 4, 5] as i (i)}
          <li class="animate-pulse rounded-xl border border-line bg-surface/40 p-4 sm:p-5">
            <div class="bg-surface-2 h-4 w-3/4 rounded"></div>
            <div class="bg-surface-2 mt-3 h-3 w-full rounded"></div>
            <div class="bg-surface-2 mt-2 h-3 w-2/3 rounded"></div>
          </li>
        {/each}
      </ul>
    {:else if error}
      <div
        role="alert"
        class="rounded-xl border border-line bg-surface/60 p-6 text-center"
      >
        <p class="text-fg text-sm">{error}</p>
        <button
          type="button"
          onclick={() => load()}
          class="text-accent hover:text-accent-soft mt-3 rounded-md border border-accent/40 px-3 py-1 text-sm transition-colors hover:border-accent"
        >
          Retry
        </button>
      </div>
    {:else if visible.length === 0}
      <p class="text-muted rounded-xl border border-line bg-surface/60 p-6 text-center text-sm">
        No stories in this feed right now.
      </p>
    {:else}
      <ul class="flex flex-col gap-3">
        {#each visible as story (story.id)}
          <li>
            <StoryCard {story} {now} />
          </li>
        {/each}
      </ul>
      <!-- End-of-feed marker: count + freshness, out of the way of the content. -->
      <p class="text-faint pt-1 text-center text-xs">
        {visible.length} {postWord} from {sourcesLabel}, summarized by AI{#if updatedLabel}&nbsp;·&nbsp;updated
          {updatedLabel}{/if}
      </p>
    {/if}
  </section>
</PullToRefresh>
