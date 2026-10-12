// Edge reference: screenshots of render cases + pixel diff data
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, 'render_cases.json'), 'utf8'));

async function main() {
  const out = {};
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 800, height: 600 });
  for (const [name, html] of Object.entries(CASES)) {
    await page.setContent(html, { timeout: 8000 });
    await page.addStyleTag({ content: 'html,body{margin:0;padding:0;background:#fff}' }).catch(() => {});
    const shot = await page.screenshot({ type: 'png' });
    fs.writeFileSync(path.join(__dirname, `edge_shot_${name}.png`), shot);
    out[name] = 'ok';
  }
  await browser.close();
  fs.writeFileSync(path.join(__dirname, 'edge_render_results.json'), JSON.stringify(out, null, 1));
  console.log('edge_render_results.json written,', Object.keys(out).length, 'cases');
}
main().catch(e => { console.error(e); process.exit(1); });
