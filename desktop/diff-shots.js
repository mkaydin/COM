// Pixel-diff two screenshot directories.
//
//   npx electron diff-shots.js <dirA> <dirB>
//
// Byte comparison is not evidence here: the captures are minutes apart and the
// database moved between them, so a page listing topics renders differently
// even when its styling is identical. This compares actual pixels.
const fs = require('fs');
const path = require('path');
const { app, nativeImage } = require('electron');

const A = process.argv[process.argv.length - 2];
const B = process.argv[process.argv.length - 1];

function diff(fileA, fileB) {
  const a = nativeImage.createFromPath(path.join(A, fileA));
  const b = nativeImage.createFromPath(path.join(B, fileB));
  if (a.isEmpty() || b.isEmpty()) return { error: 'unreadable' };
  const sa = a.getSize();
  const sb = b.getSize();
  if (sa.width !== sb.width || sa.height !== sb.height) {
    return { error: `size ${sa.width}x${sa.height} vs ${sb.width}x${sb.height}` };
  }
  const ba = a.toBitmap();
  const bb = b.toBitmap();
  let differing = 0;
  let maxDelta = 0;
  for (let i = 0; i < ba.length; i += 4) {
    const d = Math.max(
      Math.abs(ba[i] - bb[i]),
      Math.abs(ba[i + 1] - bb[i + 1]),
      Math.abs(ba[i + 2] - bb[i + 2]),
    );
    if (d > 8) differing += 1;           // ignore antialiasing noise
    if (d > maxDelta) maxDelta = d;
  }
  const pixels = ba.length / 4;
  return { pct: (differing / pixels) * 100, maxDelta, pixels };
}

app.whenReady().then(() => {
  const rows = [];
  for (const f of fs.readdirSync(A).filter((x) => x.endsWith('.png')).sort()) {
    if (!fs.existsSync(path.join(B, f))) continue;
    rows.push([f.replace('.png', ''), diff(f, f)]);
  }
  let worst = 0;
  for (const [name, r] of rows) {
    if (r.error) { console.log(`  ${name.padEnd(26)} ERROR ${r.error}`); continue; }
    worst = Math.max(worst, r.pct);
    console.log(`  ${name.padEnd(26)} ${r.pct.toFixed(3)}% pixels differ  (max channel delta ${r.maxDelta})`);
  }
  console.log(`\nworst page: ${worst.toFixed(3)}%`);
  app.exit(0);
});