// Anggaran performa (PRD §10.1): JS awal ≤ 200 KB gzip. CI gagal bila terlampaui.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const LIMIT_KB = 200;
const dist = process.argv[2] ?? "dist";
const assets = join(dist, "assets");

let total = 0;
for (const name of readdirSync(assets)) {
  const file = join(assets, name);
  if (name.endsWith(".js") && statSync(file).isFile()) {
    total += gzipSync(readFileSync(file)).length;
  }
}
const kb = (total / 1024).toFixed(1);
if (total > LIMIT_KB * 1024) {
  console.error(`Bundle JS ${kb} KB gzip melebihi anggaran ${LIMIT_KB} KB`);
  process.exit(1);
}
console.log(`Bundle JS ${kb} KB gzip (anggaran ${LIMIT_KB} KB)`);
