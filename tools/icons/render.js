// Render the bot's custom emoji set (tools/icons/icons.json) to config/emojis/<name>.png, 128x128, transparent.
// An icon marked "white": true also gets <name>_w.png: its glyph alone, white with a subtle dark outline, for green,
// red and blurple buttons, where a coloured badge would melt into the button or look muddy (the bot shows that variant
// on success, danger and primary buttons by itself: shopbot/ui/component_emoji.py).
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
const BADGE_GLYPH = 84; // the glyph inside a 120px badge
const WHITE_GLYPH = 112; // the glyph of a white variant: no badge around it, so it fills the picture
const WHITE_SUFFIX = '_w';

// An icon's optional "shift": [x, y] moves its glyph by that many units of Tabler's 24-unit grid, to centre a glyph
// that Tabler draws off-centre (e.g. a person with a small mark on the right).
function glyph(icon, shift, size = BADGE_GLYPH) {
  const file = path.join(tabler, 'icons', `${icon}.svg`);
  const [dx, dy] = shift || [0, 0];
  let svg = fs.readFileSync(file, 'utf8');
  svg = svg.replace(/<svg[^>]*>/, (tag) => {
    let out = tag
      .replace(/width="[^"]*"/, `width="${size}"`).replace(/height="[^"]*"/, `height="${size}"`)
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
  return page(body);
}

// The white variant: the badge's glyph without the badge, outlined by a soft dark edge so it also reads on a light
// background.
function whiteHtml(item) {
  return page(`<div style="display:flex;filter:drop-shadow(0 0 3px rgba(0,0,0,.5))">
    ${glyph(item.icon, item.shift, WHITE_GLYPH)}</div>`);
}

function page(body) {
  return `<html><body style="margin:0;background:transparent"><div id="c" style="width:${SIZE}px;height:${SIZE}px;
    display:flex;align-items:center;justify-content:center">${body}</div></body></html>`;
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const tab = await browser.newPage({ viewport: { width: SIZE, height: SIZE }, deviceScaleFactor: 1 });
  const pictures = [];
  for (const item of spec.icons) {
    pictures.push([item.name, html(item)]);
    if (item.white) pictures.push([item.name + WHITE_SUFFIX, whiteHtml(item)]);
  }
  for (const [name, content] of pictures) {
    await tab.setContent(content);
    await tab.locator('#c').screenshot({ path: path.join(out, `${name}.png`), omitBackground: true });
  }
  await browser.close();
  console.log(`rendered ${pictures.length} pictures (${spec.icons.length} icons) into ${out}`);
})();
