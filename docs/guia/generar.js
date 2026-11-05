// Genera docs/guia-para-ninos.pdf a partir de guia.html (necesita Node y Playwright con Chromium):
//   npm i -g playwright && npx playwright install chromium
//   node docs/guia/generar.js
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const br = await chromium.launch();
  const p = await br.newPage();
  await p.goto('file://' + path.join(__dirname, 'guia.html'));
  await p.waitForTimeout(500);
  await p.pdf({ path: path.join(__dirname, '..', 'guia-para-ninos.pdf'), format: 'A4', printBackground: true, preferCSSPageSize: true });
  await br.close();
})();
