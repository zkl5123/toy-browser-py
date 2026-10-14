// Edge (Chromium) reference: run each JS case in a real browser, capture console.log output
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

async function main() {
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'js_cases.json'), 'utf8'));
  const out = {};
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();

  for (const c of cases) {
    const logs = [];
    const pageHandler = m => { if (m.type() === 'log') logs.push(m.text()); };
    const consoleHandler = m => { logs.push(m.text()); };
    page.on('log', pageHandler);
    page.on('console', consoleHandler);

    let result;
    try {
      await page.setContent('<!DOCTYPE html><html><body><div id="t1">orig</div><button id="btn">B</button></body></html>', { timeout: 5000 });
      const val = await page.evaluate((code) => {
        try {
          const fn = new Function(code);
          fn();
          return 'OK';
        } catch (e) {
          return 'ERROR: ' + e.name + ': ' + e.message;
        }
      }, c.code).catch(e => 'EVAL_ERROR: ' + e.message);
      // let timers fire
      await new Promise(r => setTimeout(r, 30));
      result = val;
    } finally {
      page.off('log', pageHandler);
      page.off('console', consoleHandler);
    }
    out[c.id] = { status: result, logs };
  }
  await browser.close();
  fs.writeFileSync(path.join(__dirname, 'edge_js_results.json'), JSON.stringify(out, null, 1));
  console.log('edge_js_results.json written,', Object.keys(out).length, 'cases');
}

main().catch(e => { console.error(e); process.exit(1); });
