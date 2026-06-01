#!/usr/bin/env node
/**
 * Generate the social/share assets from the SVG marks (committed as binaries):
 *   static/favicon.ico        16/32/48 PNG-embedded ICO (root favicon)
 *   static/icons/og-card.png  1200x630 Open Graph / Twitter share card
 *
 * Usage:  node scripts/gen-social.mjs    (requires the `sharp` dev dependency)
 * Run it whenever the brand mark or wordmark changes; the outputs are committed.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = resolve(here, '..', 'static');
const iconsDir = resolve(staticDir, 'icons');

const { default: sharp } = await import('sharp');

// --- favicon.ico (PNG-embedded entries: 16, 32, 48) -----------------------
const faviconSvg = await readFile(resolve(iconsDir, 'favicon.svg'));
const sizes = [16, 32, 48];
const images = [];
for (const size of sizes) {
  images.push(
    await sharp(faviconSvg, { density: 384 })
      .resize(size, size, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer()
  );
}
const header = Buffer.alloc(6);
header.writeUInt16LE(0, 0); // reserved
header.writeUInt16LE(1, 2); // type: icon
header.writeUInt16LE(sizes.length, 4); // image count
let offset = 6 + 16 * sizes.length;
const entries = [];
for (let i = 0; i < sizes.length; i++) {
  const e = Buffer.alloc(16);
  e.writeUInt8(sizes[i] >= 256 ? 0 : sizes[i], 0); // width (0 == 256)
  e.writeUInt8(sizes[i] >= 256 ? 0 : sizes[i], 1); // height
  e.writeUInt8(0, 2); // palette count
  e.writeUInt8(0, 3); // reserved
  e.writeUInt16LE(1, 4); // color planes
  e.writeUInt16LE(32, 6); // bits per pixel
  e.writeUInt32LE(images[i].length, 8); // image byte size
  e.writeUInt32LE(offset, 12); // offset
  offset += images[i].length;
  entries.push(e);
}
await writeFile(resolve(staticDir, 'favicon.ico'), Buffer.concat([header, ...entries, ...images]));
console.log('[gen-social] wrote favicon.ico (16/32/48)');

// --- og-card.png (1200x630) -----------------------------------------------
const mono = "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace";
const sans = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
const card = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#0b0f14"/>
  <rect x="0" y="0" width="1200" height="8" fill="#34d399"/>
  <text x="600" y="300" text-anchor="middle" font-family="${mono}" font-size="128" font-weight="700"><tspan fill="#34d399">&#8250; </tspan><tspan fill="#e6edf3">tail -f</tspan></text>
  <text x="600" y="378" text-anchor="middle" font-family="${sans}" font-size="40" fill="#8b98a5">Top Hacker News + lobste.rs stories, summarized</text>
  <text x="600" y="560" text-anchor="middle" font-family="${mono}" font-size="30" fill="#5b6772">tailf.dev</text>
</svg>`;
await sharp(Buffer.from(card)).png().toFile(resolve(iconsDir, 'og-card.png'));
console.log('[gen-social] wrote icons/og-card.png (1200x630)');
