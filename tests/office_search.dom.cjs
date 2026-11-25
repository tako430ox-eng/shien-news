// Run with Node.js and linkedom 0.18.12 (or its standalone worker.js as .mjs).
const {test, before} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const vm = require('node:vm');

const pageFile = process.env.OFFICE_PAGE || path.join(__dirname, '../pages/shitsugyo/index.html');
const html = readFileSync(pageFile, 'utf8');
let parseHTML;
before(async () => {
  const module = process.env.OFFICE_DOM ? pathToFileURL(path.resolve(process.env.OFFICE_DOM)).href : 'linkedom';
  ({parseHTML} = await import(module));
});

function page() {
  const {document, Event} = parseHTML(html);
  const script = [...document.querySelectorAll('script')].find(el => el.textContent.includes('function plan('));
  assert.ok(script);
  // Only the DOM and form events are exercised. This suite has no navigation or layout engine.
  const window = {addEventListener() {}};
  vm.runInNewContext(script.textContent, {window, document, location:{hash:''}, URLSearchParams, Date, Math, Array, String, console});
  const id = k => document.getElementById(k);
  function fill(value) {
    id('city').value = value;
    id('city').dispatchEvent(new Event('input', {bubbles:true}));
  }
  function choose(group, value) {
    const radio = document.querySelector('input[name="' + group + '"][value="' + value + '"]');
    radio.setAttribute('checked', '');
    radio.dispatchEvent(new Event('change', {bubbles:true}));
  }
  const searches = () => [...document.querySelectorAll('a[href^="https://www.google.com/search?"]')];
  return {document, id, fill, choose, searches};
}
const terms = ['生活保護 相談窓口 電話', '生活困窮 自立相談 窓口 電話', '国民健康保険 退職 加入 窓口', '国民年金 窓口'];

test('緊急案内をたどり、地域名だけで生活保護を検索できる', () => {
  const p = page();
  assert.ok(p.document.querySelector('a[href="#city"]'));
  p.fill('八王子市');
  assert.ok(p.searches().some(link => /生活保護/.test(link.textContent) && !link.closest('[hidden]') && !link.closest('details:not([open])')));
});

test('市・区・町・村の4種類の検索と、入力の書き替え・前後の空白', () => {
  const p = page();
  for (const place of ['八王子市','東京都中央区','埼玉県小川町','埼玉県東秩父村']) {
    p.fill('　' + place + ' ');
    const values = p.searches().map(a => {
      const u = new URL(a.getAttribute('href'));
      assert.equal(u.origin + u.pathname, 'https://www.google.com/search');
      assert.deepEqual([...u.searchParams.keys()], ['q']);
      return u.searchParams.get('q');
    });
    assert.deepEqual(values.sort(), terms.map(term => place + ' ' + term).sort());
  }
});

test('地域名だけの入力・消去で、今日やることの初期案内を保つ', () => {
  const p = page();
  const initial = p.id('todayOut').innerHTML;
  p.fill('八王子市');
  assert.equal(p.id('todayOut').innerHTML, initial);
  p.fill('　 ');
  assert.deepEqual(p.searches(), []);
  assert.equal(p.id('todayOut').innerHTML, initial);
});

test('全消去で地域名・検索リンクが消え、再入力で新しい地域に切り替わる', () => {
  const p = page();
  p.fill('東京都中央区');
  assert.ok(p.searches().length);
  p.id('resetBtn').click();
  assert.equal(p.id('city').value, '');
  assert.deepEqual(p.searches(), []);
  p.fill('埼玉県東秩父村');
  assert.equal(p.searches().length, 4);
  assert.ok(p.searches().every(a => new URL(a.href).searchParams.get('q').startsWith('埼玉県東秩父村 ')));
});

test('年金免除の行には年金への近道があり、国保への近道は混ざらない', () => {
  const p = page();
  p.fill('八王子市');
  p.choose('money', 'short');
  const row = [...p.id('todayOut').querySelectorAll('li')].find(li => li.textContent.includes('免除を申請'));
  assert.ok(row);
  assert.ok(row.querySelector('a[href="#officeNenkin"]'));
  assert.equal(row.querySelector('a[href="#officeKokuho"]'), null);
  p.choose('kenpo', 'company');
  assert.equal(p.searches().length, 4);
  assert.equal(p.id('todayOut').querySelectorAll('a[href="#officeNenkin"]').length, 1);
  assert.equal(p.id('todayOut').querySelectorAll('a[href="#officeKokuho"]').length, 1);
});

test('記号や空白を含む入力と退職日は別に扱い、検索語は1個に保つ', () => {
  const p = page();
  p.id('leave').value = '2026-11-01';
  const name = " 町&区 #駅 +＜南＞ ".trim();
  p.fill(name);
  assert.equal(p.searches().length, 4);
  for (const link of p.searches()) {
    const u = new URL(link.getAttribute('href'));
    assert.deepEqual([...u.searchParams.keys()], ['q']);
    assert.ok(terms.some(t => u.searchParams.get('q') === name + ' ' + t));
    assert.equal(u.hash, '');
  }
});

test('地域欄は同名区の区別を説明し、入力なしでも窓口一覧が開ける', () => {
  const p = page();
  const hint = p.id(p.id('city').getAttribute('aria-describedby'));
  assert.ok(hint && hint.textContent.includes('東京都中央区') && hint.textContent.includes('福岡市中央区'));
  for (const href of ['https://www.j-lis.go.jp/spd/map-search/cms_1069.html','https://minna-tunagaru.jp/ichiran/']) {
    assert.ok([...p.document.querySelectorAll('a')].some(a => a.getAttribute('href') === href && !a.closest('[hidden]')));
  }
});
