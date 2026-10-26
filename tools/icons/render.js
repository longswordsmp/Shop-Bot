// Render the bot's custom emoji set (tools/icons/icons.json) to config/emojis/<name>.png, 128x128, transparent.
// Usage: node tools/icons/render.js <path to the unpacked @tabler/icons package> [output dir]
//   npm pack @tabler/icons@3.19.0 && tar xzf tabler-icons-3.19.0.tgz   -> ./package
// Uses the Playwright Chromium that renders the HTML preview (no other dependency).
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..', '..');
const tabler = process.argv[2];
const out = process.argv[3] || path.join(ROOT, 'config', 'emojis');
if (!tabler) {
  console.error('usage: node tools/icons/render.js <tabler package dir> [output dir]');
  process.exit(2);
}
const spec = JSON.parse(fs.readFileSync(path.join(__dirname, 'icons.json'), 'utf8'));
const SIZE = 128;

// An icon's optional "shift": [x, y] moves its glyph by that many units of Tabler's 24-unit grid, to centre a glyph
// that Tabler draws off-centre (e.g. a person with a small mark on the right).
function glyph(icon, shift) {
  const file = path.join(tabler, 'icons', `${icon}.svg`);
  const [dx, dy] = shift || [0, 0];
  let svg = fs.readFileSync(file, 'utf8');
  svg = svg.replace(/<svg[^>]*>/, (tag) => {
    let out = tag
      .replace(/width="[^"]*"/, 'width="84"').replace(/height="[^"]*"/, 'height="84"')
      .replace(/stroke-width="[^"]*"/, 'stroke-width="2.25"');
    if (dx || dy) out = out.replace(/viewBox="[^"]*"/, `viewBox="${-dx} ${-dy} 24 24"`);
    return out;
  });
  return svg.replace(/currentColor/g, '#FFFFFF');
}

function html(item) {
  const color = spec.colors[item.color] || item.color;
  let body;
  if (item.kind === 'dot') {
    body = `<div style="width:64px;height:64px;border-radius:50%;background:${color};"></div>`;
  } else if (item.kind === 'number') {
    body = `<div style="width:120px;height:120px;border-radius:50%;background:${color};display:flex;align-items:center;
      justify-content:center;color:#fff;font:700 78px/1 Inter, 'DejaVu Sans', sans-serif;letter-spacing:-2px">
      ${item.text}</div>`;
  } else {
    body = `<div style="width:120px;height:120px;border-radius:50%;background:${color};display:flex;align-items:center;
      justify-content:center">${glyph(item.icon, item.shift)}</div>`;
  }
  return `<html><body style="margin:0;background:transparent"><div id="c" style="width:${SIZE}px;height:${SIZE}px;
    display:flex;align-items:center;justify-content:center">${body}</div></body></html>`;
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: SIZE, height: SIZE }, deviceScaleFactor: 1 });
  for (const item of spec.icons) {
    await page.setContent(html(item));
    await page.locator('#c').screenshot({ path: path.join(out, `${item.name}.png`), omitBackground: true });
  }
  await browser.close();
  console.log(`rendered ${spec.icons.length} icons into ${out}`);
})();
