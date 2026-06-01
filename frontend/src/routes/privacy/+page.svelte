<script lang="ts">
  // Bump this whenever the policy text changes.
  const updated = '1 June 2026';
</script>

<svelte:head>
  <title>Privacy · tail -f</title>
  <link rel="canonical" href="https://tailf.dev/privacy" />
  <meta
    name="description"
    content="How tail -f handles data: no cookies, cookieless analytics, and the third parties your browser contacts."
  />
</svelte:head>

<article class="flex flex-col gap-5">
  <header class="flex flex-col gap-1">
    <h2 class="text-fg text-xl font-semibold tracking-tight">Privacy</h2>
    <p class="text-faint text-xs">Last updated: {updated}</p>
  </header>

  <p class="text-muted text-sm leading-relaxed">
    <strong class="text-fg font-medium">tail -f</strong> is a static site that shows AI-written gists of
    the top Hacker News and lobste.rs stories. It has no accounts, no logins, and
    <strong class="text-fg font-medium">sets no cookies</strong> of any kind. There is no server collecting
    your data — the page is files on a CDN.
  </p>

  <section class="flex flex-col gap-2">
    <h3 class="text-fg text-sm font-semibold">Analytics</h3>
    <p class="text-muted text-sm leading-relaxed">
      We use <a
        class="underline decoration-faint underline-offset-2 transition-colors hover:text-accent hover:decoration-accent"
        href="https://www.goatcounter.com"
        target="_blank"
        rel="noopener noreferrer">GoatCounter</a
      >, a privacy-friendly, <strong class="text-fg font-medium">cookieless</strong> analytics service, to
      count aggregate page views (and roughly: referrer and country). It sets no cookies and stores no
      persistent identifier; it does not store your IP address. Counting is skipped on
      <code class="text-faint">localhost</code>. The lawful basis is legitimate interest in understanding
      basic traffic.
    </p>
  </section>

  <section class="flex flex-col gap-2">
    <h3 class="text-fg text-sm font-semibold">Third parties your browser contacts</h3>
    <p class="text-muted text-sm leading-relaxed">
      Loading the feed makes your browser request resources from a few third parties, each of which
      necessarily sees your IP address:
    </p>
    <ul class="text-muted flex list-disc flex-col gap-1.5 pl-5 text-sm leading-relaxed">
      <li><strong class="text-fg font-medium">GoatCounter</strong> (gc.zgo.at, tailf.goatcounter.com) — the analytics script and count beacon described above.</li>
      <li><strong class="text-fg font-medium">Google</strong> (www.google.com/s2/favicons) — fetches the small favicon shown next to each story's domain; Google sees the domain being displayed.</li>
      <li><strong class="text-fg font-medium">Source sites</strong> — story thumbnail images are loaded directly from the article's own domain, so that domain sees the request.</li>
    </ul>
  </section>

  <section class="flex flex-col gap-2">
    <h3 class="text-fg text-sm font-semibold">Global Privacy Control</h3>
    <p class="text-muted text-sm leading-relaxed">
      We honor the <a
        class="underline decoration-faint underline-offset-2 transition-colors hover:text-accent hover:decoration-accent"
        href="https://globalprivacycontrol.org"
        target="_blank"
        rel="noopener noreferrer">Global Privacy Control</a
      > (the <code class="text-faint">Sec-GPC</code> signal). Because the site sets no cookies, has no
      accounts, and never sells or shares personal data, a GPC signal requires no change in behavior — there
      is nothing to opt out of.
    </p>
  </section>

  <section class="flex flex-col gap-2">
    <h3 class="text-fg text-sm font-semibold">Your data</h3>
    <p class="text-muted text-sm leading-relaxed">
      We don't collect or store personal data, so there's nothing to access, correct, or delete. If you have
      a question, email
      <a
        class="underline decoration-faint underline-offset-2 transition-colors hover:text-accent hover:decoration-accent"
        href="mailto:aaronmamparo@gmail.com">aaronmamparo@gmail.com</a
      >.
    </p>
  </section>

  <p class="text-faint text-xs">
    <a class="underline underline-offset-2 transition-colors hover:text-accent" href="/">← back to the feed</a>
  </p>
</article>
