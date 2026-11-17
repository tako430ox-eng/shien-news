// 仕事を失ったときのページ（pages/shitsugyo/）の「今日やること」と期限の計算を、実際のインラインスクリプトで確かめる。
// ブラウザ・ネットワーク・追加のパッケージは使わない。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../pages/shitsugyo/index.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.equal(scripts.length, 1);
let api;
vm.runInNewContext(scripts[0], {window: {__SHITSUGYO_TEST__: x => { api = x; }}, document: {}, Date, Math, String, Array});
const D = s => api.parseDate(s);

test('期限：会社の健康保険なら国保・年金は14日、任意継続は20日、受給期間は1年', () => {
  const ds = api.deadlines({kenpo: 'company', ins: 'yes'}, D('2026-11-30'));
  assert.match(api.label(ds[0].date), /2026年12月14日/);
  assert.ok(ds.some(d => /任意継続/.test(d.what) && /2026年12月20日/.test(api.label(d.date))));
  assert.ok(ds.some(d => /受給期間/.test(d.what) && /2027年11月30日/.test(api.label(d.date))));
});
test('期限：もともと国保・家族の扶養の人には切り替えの期限を出さない', () => {
  for (const kenpo of ['kokuho', 'family']) {
    const ds = api.deadlines({kenpo, ins: 'yes'}, D('2026-11-30'));
    assert.ok(!ds.some(d => /国民健康保険|国民年金|任意継続/.test(d.what)), kenpo);
  }
});
test('期限：雇用保険に入っていなかった人には受給期間を出さない', () => {
  assert.ok(!api.deadlines({kenpo: 'company', ins: 'no'}, D('2026-11-30')).some(d => /受給期間/.test(d.what)));
});
test('期限：月末・うるう年・年をまたぐ日付', () => {
  assert.match(api.label(api.deadlines({kenpo: 'company', ins: 'yes'}, D('2027-12-25'))[0].date), /2028年1月8日/);
  assert.match(api.label(api.deadlines({kenpo: 'company', ins: 'yes'}, D('2028-02-20'))[0].date), /2028年3月5日/);
});
test('退職日がないとき・形が違うときは期限を出さない', () => {
  assert.equal(api.deadlines({}, null).length, 0);
  assert.equal(api.parseDate('2026/11/30'), null);
  assert.equal(api.parseDate('<img>'), null);
});
test('まだ辞めていない：署名しないことと労働相談が今日やること', () => {
  const p = api.plan({why: 'notyet'}, null, D('2026-11-18'));
  assert.match(p.today, /署名しない/);
  assert.match(p.today, /総合労働相談コーナー/);
});
test('お金が足りない：今日やることは自立相談支援機関への電話', () => {
  const p = api.plan({why: 'self', money: 'short', kenpo: 'company', ins: 'yes'}, D('2026-11-10'), D('2026-11-18'));
  assert.match(p.today, /自立相談支援/);
  assert.ok(p.next.some(x => /国民健康保険と国民年金/.test(x)));
});
test('会社都合で会社の健康保険：役所の切り替えが先、国保の軽減と年金の免除も続けて出る', () => {
  const p = api.plan({why: 'company', kenpo: 'company', ins: 'yes', money: 'ok'}, D('2026-11-10'), D('2026-11-18'));
  assert.match(p.today, /国民健康保険と国民年金/);
  assert.ok(p.next.some(x => /軽減/.test(x)));
  assert.ok(p.next.some(x => /免除/.test(x)));
  assert.ok(p.next.some(x => /ハローワーク/.test(x)));
});
test('14日を過ぎたら「過ぎていても早く」と言う', () => {
  const p = api.plan({why: 'self', kenpo: 'company', ins: 'yes'}, D('2026-10-01'), D('2026-11-18'));
  assert.match(p.today, /14日を過ぎていても/);
});
test('雇用保険なし：求職者支援制度を案内し、もともと国保なら役所の切り替えは出さない', () => {
  const p = api.plan({why: 'self', kenpo: 'kokuho', ins: 'no'}, D('2026-11-10'), D('2026-11-18'));
  assert.match(p.today, /求職者支援制度/);
  assert.ok(![p.today, ...p.next].some(x => /切り替え/.test(x)));
});
test('どの組み合わせでも今日やることが一つ出る', () => {
  for (const why of ['', 'company', 'self', 'notyet', 'unknown'])
    for (const ins of ['', 'yes', 'no', 'unknown'])
      for (const kenpo of ['', 'company', 'kokuho', 'family', 'unknown'])
        for (const money of ['', 'short', 'ok'])
          for (const leave of [null, D('2026-11-10'), D('2026-09-01')]) {
            const p = api.plan({why, ins, kenpo, money}, leave, D('2026-11-18'));
            assert.ok(p.today && p.today.length > 10, JSON.stringify({why, ins, kenpo, money}));
          }
});
test('ページは外のサーバーから読み込まず、入力を送る仕組み（fetch・送信フォーム・保存）を持たない', () => {
  assert.doesNotMatch(html, /<script[^>]+src=/);
  assert.doesNotMatch(scripts[0], /fetch\(|XMLHttpRequest|sendBeacon|localStorage|sessionStorage|document\.cookie/);
  assert.doesNotMatch(html, /<form\b/);
});
