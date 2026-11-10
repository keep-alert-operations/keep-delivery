import { readFile, writeFile } from 'node:fs/promises';
const root = new URL('./', import.meta.url);
const read = (name) => readFile(new URL(name, root), 'utf8');
const chapters = [
  ['start', 'Start here'], ['architecture', 'Architecture & repositories'],
  ['pipeline', 'Pipeline & replay lab'], ['domain-model', 'Domain state playground'],
  ['read-write', 'Commands, search & live'], ['local-development', 'Local development'],
  ['change-workflow', 'Make your first change'], ['verification', 'Tests & evidence'],
  ['identity-security', 'Identity & security'], ['operations', 'Operations & recovery'],
  ['release-readiness', 'Release readiness'], ['labs', 'Practice & quizzes'],
  ['sources', 'Glossary & sources'],
];
let content = (await Promise.all(['system','development','operations','labs'].map(name => read(`parts/${name}.html`)))).join('\n');
for (const [id, title] of chapters) {
  content = content.replace(new RegExp(`<section id="${id}"(?! data-title)`), `<section id="${id}" data-title="${title.replaceAll('&','&amp;')}"`);
}
const nav = chapters.map(([id,title],i) => `${[0,5,8,11].includes(i)?`<p class="nav-group">${({0:'Understand',5:'Build',8:'Operate',11:'Practise'})[i]}</p>`:''}<a href="#${id}">${String(i).padStart(2,'0')} · ${title.replaceAll('&','&amp;')}</a>`).join('\n');
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Interactive Keep developer onboarding: architecture, replay, domain behavior, development, security and operations."><title>Keep · Developer onboarding</title><style>${await read('guide.css')}</style></head>
<body><a class="skip" href="#start">Skip to guide</a><div class="layout"><aside class="sidebar" aria-label="Guide navigation"><div class="brand">keep<span>.</span></div><p class="subtitle">DEVELOPER FIELD GUIDE · 2026.10</p><label for="topic-search">Find a topic</label><input id="topic-search" type="search" placeholder="Try replay or migration…" autocomplete="off"><p id="search-count" class="search-status" aria-live="polite"></p><div id="search-results" hidden></div><nav aria-label="Chapters">${nav}</nav><div class="progress-area"><label for="reading-progress" id="reading-progress-text">Reading progress</label><progress id="reading-progress" value="0" max="11"></progress><small id="progress-storage"></small><button id="reset-progress">Reset reading progress</button></div></aside><main id="main"><noscript><p class="noscript">Enable JavaScript for the simulators, quiz feedback, topic search and local progress. The explanatory chapters and source links remain readable.</p></noscript>${content}<footer class="footer">Keep team onboarding · Offline edition · 11 October 2026</footer></main></div><script>${await read('guide.js')}</script></body></html>`;
await writeFile(new URL('index.html',root),html);
console.log(`Built ${chapters.length} chapters, ${Buffer.byteLength(html).toLocaleString()} bytes → index.html`);
