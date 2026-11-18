// 家賃編（pages/yachin/index.html）の「最初に出る画面」を、公開する HTML に前もって書き込む。
//
// なぜ：家賃編は、今日やること・手順・制度の候補を、ページの中のプログラム（JavaScript）で表示している。
// 元の HTML ではその場所が空なので、プログラムを動かさない検索エンジンや AI の検索、読み込みに失敗した
// ブラウザには、いちばん大事な本文（電話で言う文、手順、制度の名前）が見えない。
// そこで公開するときだけ、何も選んでいない状態の表示をここで作って、空の場所に入れておく。
// ブラウザでプログラムが動けば、これまでどおり同じ場所が上書きされる（見た目と動きは変わらない）。
//
// 使い方：node scripts/prerender_yachin.cjs < 元のHTML > 書き込んだHTML
// scripts/build.py が公開用のサイトを作るときに呼ぶ。node がない、または失敗したときは、元の HTML をそのまま公開する。
// 正本は pages/yachin/index.html のまま。このスクリプトは正本を書き換えない。
const vm = require('node:vm');

// 書き込む場所（id）。何も選んでいない状態でプログラムが入れる中身と同じものを入れる。
const TARGETS = ['questions', 'afterOpts', 'srcList', 'urgentOut', 'firstOut', 'findOut', 'candOut', 'exclOut', 'planOut'];
// 窓口が開いている平日の昼（2026-11-18 水曜 10:00 日本時間）として作る。
// 「いまは役所が閉まっている時間かもしれません」のような、開いた時刻で変わる文を本文に固定しないため。
const WHEN = '2026-11-18T01:00:00Z';

function render(html) {
  const m = html.match(/<script>\s*([\s\S]*?)<\/script>/);
  if (!m) throw new Error('家賃編の中のプログラム（<script>）が見つかりません');
  const inline = m[1];
  const cut = inline.lastIndexOf('  renderQuestions();');
  if (cut < 0) throw new Error('家賃編のプログラムの始まり（renderQuestions();）が見つかりません');
  const hooks = 'globalThis.__pre={freshState,decide,renderQuestions,renderAfterOpts,renderSources,renderUrgent,renderFirst,renderFind,renderCandidates,renderPlan};';
  const source = inline.slice(0, cut) + hooks + '\n})();';
  const elements = new Map();
  function el(id) {
    if (!elements.has(id)) elements.set(id, {id, innerHTML: '', textContent: '', value: '', hidden: false, checked: false, open: false, style: {}, children: [],
      classList: {toggle() {}, add() {}, remove() {}, contains() { return false; }}, querySelectorAll() { return []; }, querySelector() { return null; },
      addEventListener() {}, setAttribute() {}, getAttribute() { return null; }, removeAttribute() {}, focus() {}, scrollIntoView() {}});
    return elements.get(id);
  }
  class Clock extends Date { constructor(...a) { super(...(a.length ? a : [WHEN])); } static now() { return new Date(WHEN).getTime(); } }
  const context = {Date: Clock, console, setTimeout: () => 0, clearTimeout() {}, navigator: {},
    document: {getElementById: el, querySelectorAll: () => [], querySelector: () => null, addEventListener() {}},
    window: {addEventListener() {}}, location: {hash: '', href: ''},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}}};
  vm.createContext(context);
  vm.runInContext(source, context);
  const a = context.__pre;
  a.renderQuestions(); a.renderAfterOpts(); a.renderSources(); a.renderUrgent(); a.renderFirst(); a.renderFind();
  const d = a.decide();
  a.renderCandidates(d); a.renderPlan(d);
  const out = {};
  for (const id of TARGETS) out[id] = el(id).innerHTML;
  return out;
}

function prerender(html) {
  const parts = render(html);
  let result = html;
  for (const id of TARGETS) {
    if (!parts[id]) continue;
    // 中身が空の要素（<div id="firstOut" …></div>）だけに入れる。見つからない・2つ以上あるときは止める。
    const re = new RegExp('(<(\\w+)\\b[^>]*\\bid="' + id + '"[^>]*>)(</\\2>)', 'g');
    const hits = result.match(re) || [];
    if (hits.length !== 1) throw new Error(`書き込む場所 id="${id}" が、中身の空の要素として1つだけ見つかりません（${hits.length}件）`);
    result = result.replace(re, (_, open, tag, close) => open + parts[id] + close);
  }
  return result.replace('<!doctype html>', '<!doctype html>\n<!-- 今日やること・手順・制度の候補は、公開時に scripts/prerender_yachin.cjs が「何も選んでいない状態」で書き込んだもの。ブラウザではプログラムが同じ場所を作り直す。 -->');
}

module.exports = {prerender, render, TARGETS};

if (require.main === module) {
  let input = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', c => { input += c; });
  process.stdin.on('end', () => { process.stdout.write(prerender(input)); });
}
