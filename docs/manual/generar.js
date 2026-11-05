// Genera docs/manual-administracion.pdf a partir de manual.html (necesita Node y Playwright con Chromium):
//   npm i -g playwright && npx playwright install chromium
//   node docs/manual/generar.js
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const br = await chromium.launch();
  const p = await br.newPage();
  await p.goto('file://' + path.join(__dirname, 'manual.html'));
  await p.waitForTimeout(500);
  await p.pdf({ path: path.join(__dirname, '..', 'manual-administracion.pdf'), format: 'A4', printBackground: true, preferCSSPageSize: true });
  await br.close();
})();
