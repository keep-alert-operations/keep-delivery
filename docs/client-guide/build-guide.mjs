// Build the offline reading edition from the adjacent Markdown chapters.
// Usage: node build-guide.mjs [path/to/marked/lib/marked.esm.js]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const { marked } = process.argv[2]
  ? await import(pathToFileURL(path.resolve(process.argv[2])).href)
  : await import('marked');
const chapters = [
  ['README.md', 'start', 'Guide contents'],
  ['01-getting-started.md', 'getting-started', '1 · Getting started'],
  ['02-feed-and-search.md', 'feed', '2 · Feed and search'],
  ['03-working-alerts.md', 'alerts', '3 · Working alerts'],
  ['04-incidents.md', 'incidents', '4 · Incidents'],
  ['05-presets-and-dashboards.md', 'views', '5 · Presets and dashboards'],
  ['06-maintenance-and-correlation.md', 'automation', '6 · Maintenance and correlation'],
  ['07-troubleshooting-and-glossary.md', 'help', '7 · Troubleshooting and glossary'],
];
const ids = Object.fromEntries(chapters.map(([file, id]) => [file, id]));
const slug = (s) => s.toLowerCase().replace(/<[^>]+>/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const sections = chapters.map(([file, id], index) => {
  let source = fs.readFileSync(path.join(root, file), 'utf8');
  source = source.split('\n## Reference')[0];
  if (index === 0) {
    source = source.replace(/The \[illustrated reading edition\]\(index.html\)[^\n]+/, 'Use the contents menu to jump between chapters. Use your browser’s Find command to search the whole guide and Print to create a paper or PDF copy.');
    source = source.replace(/ Its Markdown sources[^\n]+/, '');
  }
  let html = marked.parse(source, { gfm: true });
  html = html.replace(/<img src="(assets\/[^\"]+)" alt="([^\"]*)"[^>]*>/g, (_, asset, alt) => {
    const bytes = fs.readFileSync(path.join(root, asset));
    const mime = asset.endsWith('.svg') ? 'image/svg+xml' : 'image/png';
    const src = `data:${mime};base64,${bytes.toString('base64')}`;
    return `<img src="${src}" alt="${alt}"><details class="figure-detail"><summary>View full-size illustration</summary><div class="figure-scroll" tabindex="0"><img src="${src}" alt="${alt}"></div></details>`;
  });
  html = html.replace(/href="([^"#]+\.md)(?:#[^"]*)?"/g, (match, file) => ids[file] ? `href="#${ids[file]}"` : match);
  html = html.replace(/<h([1-6])>(.*?)<\/h\1>/g, (_, level, title) => {
    const n = Math.min(6, Number(level) + (index ? 1 : 0));
    return `<h${n} id="${id}-${slug(title)}">${title}</h${n}>`;
  });
  html = html.replace(/<table>/g, '<div class="table-wrap" tabindex="0"><table>').replace(/<\/table>/g, '</table></div>');
  return `<article id="${id}" aria-label="${chapters[index][2]}">${html}</article>`;
}).join('\n');
const nav = chapters.map(([, id, label]) => `<a href="#${id}">${label}</a>`).join('\n');
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Appchi daily user guide</title><style>
:root{color-scheme:light;--ink:#1f312e;--muted:#5a6c66;--green:#246355;--line:#dce4df;--paper:#fffefa}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:#f0f3ee;color:var(--ink);font:17px/1.7 system-ui,-apple-system,"Segoe UI",sans-serif}a{color:var(--green);text-underline-offset:3px}a:hover{color:#103b30}a:focus-visible{outline:3px solid #bd811c;outline-offset:4px}aside{position:fixed;inset:0 auto 0 0;width:285px;padding:35px 24px;overflow:auto;background:#183f36;color:white}aside b{display:block;font-size:29px;letter-spacing:-1px}aside p{color:#c9ded3;font-size:14px;margin:4px 0 27px}nav a{display:block;padding:10px 8px;color:#edf8f1;text-decoration:none;font-size:15px;line-height:1.5;border-radius:5px}nav a:hover,nav a:focus{color:white;background:#2a584c}aside small{display:block;color:#c9ded3;font-size:12px;margin-top:28px}main{max-width:1230px;margin-left:285px;padding:45px 56px 80px}article{background:var(--paper);border:1px solid var(--line);border-radius:16px;margin-bottom:32px;padding:36px 44px;overflow-wrap:anywhere;scroll-margin-top:24px}article:first-child{border-top:7px solid #bf9049}h1{font-size:43px;line-height:1.16;letter-spacing:-1.4px;margin:0 0 22px}h2{font-size:32px;line-height:1.25;letter-spacing:-.7px;margin:0 0 24px}h3{font-size:23px;line-height:1.35;margin:37px 0 13px}h4{font-size:19px;margin:28px 0 10px}p{margin:14px 0}li{padding-left:4px;margin:8px 0}ul,ol{padding-left:25px}strong{color:#1d4639}img{width:100%;height:auto;display:block;border:1px solid var(--line);border-radius:10px;margin:24px 0 10px}a.figure-link{display:block}p:has(>em:only-child){font-size:14px;color:var(--muted);line-height:1.5;margin-top:10px}.table-wrap{overflow:auto;margin:22px 0;border:1px solid var(--line);border-radius:9px}table{width:100%;border-collapse:collapse;font-size:15px;line-height:1.55}th{text-align:left;background:#e9f0e9;color:#234b3d}td,th{padding:13px 15px;vertical-align:top;min-width:110px;border-bottom:1px solid var(--line)}tr:last-child td{border-bottom:0}tr:nth-child(even) td{background:#fafbf6}code{font: .88em/1.5 Consolas,monospace;background:#eef0e9;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}pre{overflow:auto;padding:16px;background:#eef0e9;border-radius:8px}blockquote{border-left:4px solid #be9654;margin:23px 0;padding:2px 21px;background:#fbf3e2}article>p:first-of-type:has(a){font-size:14px;color:var(--muted)}footer{font-size:13px;color:var(--muted);padding:0 10px}
.figure-detail{font-size:14px;color:var(--green)}.figure-detail summary{cursor:pointer;padding:4px 0}.figure-scroll{overflow:auto;max-height:75vh;border:1px solid var(--line);margin-top:10px}.figure-scroll img{width:1100px;max-width:none;margin:0;border:0;border-radius:0}
@media(max-width:1100px){aside{width:235px;padding:25px 15px}main{margin-left:235px;padding:25px}article{padding:28px}h1{font-size:36px}}
@media(max-width:760px){aside{position:relative;width:auto;padding:22px}aside b{font-size:25px}aside p{margin-bottom:12px}nav{display:grid;grid-template-columns:1fr 1fr;gap:2px 10px}nav a{font-size:13px;padding:7px 0}aside small{margin-top:12px}main{margin:0;padding:16px}article{padding:22px 18px;border-radius:11px;margin-bottom:18px}h1{font-size:32px}h2{font-size:27px}h3{font-size:21px}body{font-size:16px}.table-wrap{font-size:14px}td,th{padding:10px;min-width:125px}}
@media print{body{background:white;font-size:10pt;line-height:1.5}aside{display:none}main{margin:0;padding:0;max-width:none}article{border:0!important;border-radius:0;padding:0;margin:0;break-before:page;overflow:visible}article:first-child{break-before:auto}h1{font-size:28pt}h2{font-size:23pt}h3{font-size:16pt;break-after:avoid}h4{break-after:avoid}img{max-height:180mm;object-fit:contain;break-inside:avoid}.table-wrap{overflow:visible;border:0}table{font-size:9pt}tr{break-inside:avoid}a{color:inherit}.figure-link{break-inside:avoid}footer{margin-top:20px}@page{size:A4;margin:17mm}}
@media print{.figure-detail{display:none}}
</style></head><body><aside><b>Appchi</b><p>Daily user guide · 11 October 2026</p><nav aria-label="Guide chapters">${nav}</nav><small>Use your browser’s Find to search.<br>Illustrations use example data.<br>All images are included for offline reading.</small></aside><main>${sections}<footer>Appchi daily user guide · Prepared from current system Markdown specifications and UI documentation. Administration and integration setup are outside this guide.</footer></main></body></html>`;
fs.writeFileSync(path.join(root, 'index.html'), html, 'utf8');
console.log(`Built ${chapters.length - 1} chapters; ${html.length.toLocaleString()} characters, with embedded images.`);
