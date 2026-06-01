<script lang="ts">
  import type { Story, Source } from '$lib/types';
  import { relativeTime, absoluteTime } from '$lib/time';

  let { story, now = Date.now() }: { story: Story; now?: number } = $props();

  // Display label per source for the "via …" attribution.
  const SOURCE_LABELS: Record<Source, string> = {
    hn: 'Hacker News',
    lobsters: 'lobste.rs'
  };

  // Title (and image) link to the source article when there is one; otherwise
  // to the primary (oldest) discussion (self/text posts have no external URL).
  const primaryHref = $derived(story.url ?? story.discussions[0]?.comments_url ?? '#');
  const isExternal = $derived(story.url !== null);

  const rel = $derived(relativeTime(story.published, now));
  const abs = $derived(absoluteTime(story.published));

  // Favicon via a public service keyed on the domain (no backend storage).
  const faviconSrc = $derived(
    story.domain
      ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(story.domain)}&sz=64`
      : null
  );

  // Hide the image / favicon if the URL 404s or fails to load.
  let imgFailed = $state(false);
  let faviconFailed = $state(false);
  const showImage = $derived(Boolean(story.image) && !imgFailed);
</script>

<article
  class="group flex flex-col overflow-hidden rounded-xl border border-line bg-surface/60 transition-colors hover:border-accent/40 hover:bg-surface-2/60"
>
  <!-- 1. Title -->
  <h2 class="px-4 pt-4 text-base leading-snug font-semibold sm:px-5 sm:pt-5 sm:text-lg">
    <a
      href={primaryHref}
      target="_blank"
      rel="noopener noreferrer"
      class="decoration-accent/0 hover:text-accent-soft hover:underline hover:decoration-accent-soft/60 hover:underline-offset-2"
    >
      {story.title}
      {#if !isExternal}
        <span class="text-faint align-middle text-xs font-normal">(discussion)</span>
      {/if}
    </a>
  </h2>

  <!-- 2. Image (only when present; cards without one simply skip it) -->
  {#if showImage}
    <a href={primaryHref} target="_blank" rel="noopener noreferrer" class="mt-3 block">
      <img
        src={story.image}
        alt=""
        loading="lazy"
        onerror={() => (imgFailed = true)}
        class="aspect-[1.91/1] w-full bg-surface-2 object-cover"
      />
    </a>
  {/if}

  <!-- 3. Gist -->
  {#if story.gist}
    <p class="text-muted mt-3 px-4 text-sm leading-relaxed sm:px-5 sm:text-[0.95rem]">
      {story.gist.text}
      {#if story.gist.kind !== 'article'}
        <span
          class="text-faint ml-1 align-middle text-[0.65rem] tracking-wide uppercase"
          title={`gist source: ${story.gist.kind}`}
        >
          {story.gist.kind.replace('_', ' ')}
        </span>
      {/if}
    </p>
  {/if}

  <!-- 4. Footer (one line): {favicon} {domain} via {source}, {source} … | {time ago}.
       Favicon + domain are plain; each source name links to its own comments. -->
  <div
    class="text-faint mt-3 flex items-center gap-x-3 px-4 pb-4 text-xs sm:px-5 sm:pb-5"
  >
    <span class="text-muted flex min-w-0 items-center gap-1.5 font-mono">
      {#if faviconSrc && !faviconFailed}
        <img
          src={faviconSrc}
          alt=""
          width="16"
          height="16"
          loading="lazy"
          onerror={() => (faviconFailed = true)}
          class="h-4 w-4 shrink-0 rounded-sm"
        />
      {/if}
      {#if story.domain}
        <span class="truncate">{story.domain}</span>
      {/if}
      <span class="shrink-0">via</span>
      <span class="flex shrink-0 items-center gap-1">
        {#each story.discussions as d, i (d.source + d.comments_url)}
          {#if i > 0}<span aria-hidden="true">,</span>{/if}
          <a
            href={d.comments_url}
            target="_blank"
            rel="noopener noreferrer"
            class="hover:text-accent"
          >
            {SOURCE_LABELS[d.source] ?? d.source}
          </a>
        {/each}
      </span>
    </span>

    {#if rel}
      <time class="ml-auto shrink-0" datetime={story.published} title={abs}>{rel}</time>
    {/if}
  </div>
</article>
