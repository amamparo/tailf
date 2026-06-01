#!/usr/bin/env node
/**
 * Rasterize the tailf SVG marks into the PWA PNG set.
 *
 * Source of truth: static/icons/icon.svg (+ icon-maskable.svg, favicon.svg).
 * Outputs (into static/icons/):
 *   icon-192.png             192x192   (manifest "any")
 *   icon-512.png             512x512   (manifest "any")
 *   icon-512-maskable.png    512x512   (manifest "maskable", safe-zone padded source)
 *   apple-touch-icon-180.png 180x180   (iOS home screen)
 *   favicon-32.png           32x32     (classic favicon fallback)
 *
 * Usage:  node scripts/gen-icons.mjs       (requires the `sharp` dev dependency)
 *
 * Note: this is the portable fallback generator. If a system rasterizer
 * (rsvg-convert / ImageMagick / a headless browser) is available, the build/
 * CI can produce the same PNGs; the SVGs are the canonical source either way.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const iconsDir = resolve(here, '..', 'static', 'icons');

/** @type {Array<{src: string, out: string, size: number}>} */
const targets = [
  { src: 'icon.svg', out: 'icon-192.png', size: 192 },
  { src: 'icon.svg', out: 'icon-512.png', size: 512 },
  { src: 'icon-maskable.svg', out: 'icon-512-maskable.png', size: 512 },
  { src: 'icon.svg', out: 'apple-touch-icon-180.png', size: 180 },
  { src: 'favicon.svg', out: 'favicon-32.png', size: 32 },
];

async function main() {
  let sharp;
  try {
    ({ default: sharp } = await import('sharp'));
  } catch {
    console.error(
      '[gen-icons] `sharp` is not installed. Run `pnpm add -D sharp` (or rasterize ' +
        'the SVGs in static/icons/ with rsvg-convert/ImageMagick) to produce the PNG set.'
    );
    process.exitCode = 1;
    return;
  }

  for (const { src, out, size } of targets) {
    const svg = await readFile(resolve(iconsDir, src));
    const png = await sharp(svg, { density: 384 })
      .resize(size, size, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer();
    await writeFile(resolve(iconsDir, out), png);
    console.log(`[gen-icons] wrote ${out} (${size}x${size})`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
