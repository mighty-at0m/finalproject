const puppeteer = require('puppeteer');
const path = require('path');
(async () => {
  const browser = await puppeteer.launch({ args: ['--no-sandbox','--disable-setuid-sandbox'] });
  const page = await browser.newPage();
  const file = 'file://' + path.resolve(__dirname, 'glow_demo.html');
  await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 2 });
  await page.goto(file, { waitUntil: 'networkidle2' });
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'glow_demo_screenshot.png', fullPage: true });
  await browser.close();
  console.log('Saved glow_demo_screenshot.png');
})();
