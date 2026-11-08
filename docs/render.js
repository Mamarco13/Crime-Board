// Renderiza docs/architecture.svg a PNG.
// Uso: 1) npm install @resvg/resvg-js   2) node docs/render.js
const { Resvg } = require('@resvg/resvg-js');
const fs = require('fs');
const path = require('path');

const dir = __dirname;
const svg = fs.readFileSync(path.join(dir, 'architecture.svg'), 'utf8');

const resvg = new Resvg(svg, {
  fitTo: { mode: 'width', value: 2400 },
  font: { loadSystemFonts: true, defaultFontFamily: 'Segoe UI' },
  background: '#ffffff',
});

const png = resvg.render().asPng();
const out = path.join(dir, 'architecture.png');
fs.writeFileSync(out, png);
console.log('PNG escrito en:', out, `(${png.length} bytes)`);
