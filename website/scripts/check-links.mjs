import { readFile, readdir, stat } from 'node:fs/promises';
import { resolve, join, relative } from 'node:path';

const root = resolve('dist');
const base = '/project-lifecycle-agent/';
const errors = [];
let count = 0;
const htmlFiles = [];
async function walk(folder) {
  for (const item of await readdir(folder, { withFileTypes: true })) {
    const path = join(folder, item.name);
    if (item.isDirectory()) await walk(path);
    else if (item.name.endsWith('.html')) htmlFiles.push(path);
  }
}
await walk(root);
const cache = new Map();
async function content(path) {
  if (!cache.has(path)) cache.set(path, await readFile(path, 'utf8'));
  return cache.get(path);
}
for (const file of htmlFiles) {
  const html = await content(file);
  const route = base + relative(root, file).replaceAll('\\', '/').replace(/index\.html$/, '');
  for (const match of html.matchAll(/\b(?:href|src)=["']([^"']+)["']/g)) {
    const value = match[1].replaceAll('&amp;', '&');
    if (/^(https?:|\/\/|data:|mailto:|tel:|javascript:)/.test(value)) continue;
    const url = new URL(value, `https://local.invalid${route}`);
    if (!url.pathname.startsWith(base)) {
      errors.push(`${relative(root, file)}: outside base path: ${value}`); continue;
    }
    let target = resolve(root, decodeURIComponent(url.pathname.slice(base.length)));
    if (target !== root && !target.startsWith(root + (process.platform === 'win32' ? '\\' : '/'))) {
      errors.push(`${relative(root, file)}: unsafe path: ${value}`); continue;
    }
    try {
      if ((await stat(target)).isDirectory()) target = join(target, 'index.html');
      await stat(target);
      if (url.hash && target.endsWith('.html')) {
        const id = decodeURIComponent(url.hash.slice(1));
        const text = await content(target);
        if (!text.includes(`id="${id}"`) && !text.includes(`id='${id}'`)) {
          errors.push(`${relative(root, file)}: missing fragment: ${value}`);
        }
      }
      count++;
    } catch {
      errors.push(`${relative(root, file)}: missing target: ${value}`);
    }
  }
}
if (errors.length) {
  console.error(errors.join('\n')); process.exit(1);
}
console.log(`Verified ${htmlFiles.length} HTML pages and ${count} local links/assets.`);
