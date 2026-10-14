// Edge reference: getBoundingClientRect of probe elements per layout case
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

const PROBE = {
  layout_blocks: ['a', 'b'],
  layout_margin_collapse: ['t', 'b'],
  layout_flex: ['row', 'c1', 'c2', 'c3'],
  layout_grid: ['g', 'g1', 'g4'],
  layout_table: ['tab'],
  layout_abs: ['p', 'c'],
  layout_text_wrap: ['w'],
};

async function main() {
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'layout_cases.json'), 'utf8'));
  const out = {};
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--window-size=800,600'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 800, height: 600 });
  for (const [name, html] of Object.entries(cases)) {
    await page.setContent(html, { timeout: 5000 });
    const rect = async id => {
      const r = await page.evaluate(i => {
        const el = document.getElementById(i);
        if (!el) return null;
        const b = el.getBoundingClientRect();
        return { x: b.x, y: b.y, w: b.width, h: b.height };
      }, id);
      if (r) { r.x = +r.x.toFixed(2); r.y = +r.y.toFixed(2); r.w = +r.w.toFixed(2); r.h = +r.h.toFixed(2); }
      return r;
    };
    out[name] = {};
    for (const id of PROBE[name]) out[name][id] = await rect(id);
  }
  await browser.close();
  fs.writeFileSync(path.join(__dirname, 'edge_layout_results.json'), JSON.stringify(out, null, 1));
  console.log('edge_layout_results.json written,', Object.keys(out).length, 'cases');
}
main().catch(e => { console.error(e); process.exit(1); });
