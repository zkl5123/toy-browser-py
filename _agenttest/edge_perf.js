// Edge perf: time toContentLoad for 1000 / 5000 element pages
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

function buildDoc(n) {
  const rows = Array.from({length: n}, (_, i) =>
    `<div style="width:100px;height:20px;margin:2px;background:#ccc">row ${i}</div>`).join('');
  return `<html><body>${rows}</body></html>`;
}

async function main() {
  const out = {};
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  for (const n of [1000, 5000]) {
    const t0 = Date.now();
    await page.setContent(buildDoc(n), { timeout: 30000 });
    await page.evaluate(() => new Promise(r => requestAnimationFrame(r)));
    out[`perf_${n}`] = { ms: Date.now() - t0 };
    console.log(n, out[`perf_${n}`].ms, 'ms');
  }
  await browser.close();
  fs.writeFileSync(path.join(__dirname, 'edge_perf.json'), JSON.stringify(out, null, 1));
}
main().catch(e => { console.error(e); process.exit(1); });
