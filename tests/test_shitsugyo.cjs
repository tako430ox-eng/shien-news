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

// 任意継続の期待値は、実装からではなく次の規則と暦から決めた（2026-11-19 確認）。
//  協会けんぽ「退職日の翌日から20日以内（20日目が土日、祝日の場合は翌営業日）」。
//  20日目＝退職日の翌日を1日目として数えた20日目＝退職日＋20日。祝日は内閣府「国民の祝日について」の2026・2027年の表。
const nini = (leave, kenpo = 'company') => api.deadlines({kenpo, ins: 'yes'}, D(leave)).find(d => /任意継続/.test(d.what));
test('期限：会社の健康保険なら国保・年金は14日、任意継続は20日（休日なら翌営業日）、受給期間は1年', () => {
  const ds = api.deadlines({kenpo: 'company', ins: 'yes'}, D('2026-11-30'));
  assert.match(api.label(ds[0].date), /2026年12月14日/);
  // 20日目は 2026-12-20（日）→ 翌営業日 12-21（月）
  assert.match(api.label(nini('2026-11-30').date), /2026年12月21日（月）/);
  assert.ok(ds.some(d => /受給期間/.test(d.what) && /2027年11月30日/.test(api.label(d.date))));
});
test('任意継続：20日目が土日・祝日・連休のときは翌営業日', () => {
  // 外部点検の例：2026-10-26 退職 → 20日目 11-15（日）→ 11-16（月）
  assert.match(api.label(nini('2026-10-26').date), /2026年11月16日（月）/);
  // 20日目が平日なら動かさない：2026-11-04 退職 → 11-24（火）
  assert.match(api.label(nini('2026-11-04').date), /2026年11月24日（火）/);
  // 20日目が祝日（2027-01-11 成人の日）→ 01-12（火）
  assert.match(api.label(nini('2026-12-22').date), /2027年1月12日（火）/);
  // 20日目 2026-05-03（日）、5/4・5/5・5/6 も休み → 05-07（木）
  assert.match(api.label(nini('2026-04-13').date), /2026年5月7日（木）/);
  // 20日目 2026-09-19（土）、9/20（日）9/21（敬老の日）9/22（休日）9/23（秋分の日）→ 09-24（木）
  assert.match(api.label(nini('2026-08-30').date), /2026年9月24日（木）/);
  // 動かしたときは、20日目の日付も書く
  assert.match(nini('2026-10-26').note, /20日目の2026年11月15日（日）/);
  assert.ok(nini('2026-10-26').sure);
});
test('任意継続：年末年始や祝日の表がない年は「確かな期限」として出さない', () => {
  // 20日目 2026-12-29（火）：年末年始の扱いは公式資料に書かれていない
  const ye = nini('2026-12-09');
  assert.match(api.label(ye.date), /2026年12月29日/);
  assert.equal(ye.sure, false);
  assert.match(ye.note, /年末年始/);
  // 2028年の祝日はまだ公表されていない
  const y28 = nini('2027-12-25');
  assert.equal(y28.sure, false);
  assert.match(y28.note, /公表されていない/);
});
test('期限：郵送は20日以内に届くようにと書く', () => {
  assert.match(nini('2026-11-30').note, /郵送は20日以内に届く/);
});
test('期限：健康保険が「分からない」・未回答なら「会社の健康保険だった場合」と断る', () => {
  for (const kenpo of ['unknown', '']) {
    const ds = api.deadlines({kenpo, ins: 'yes'}, D('2026-11-30'));
    assert.ok(ds.filter(d => /国民健康保険|国民年金|任意継続/.test(d.what)).every(d => /会社の健康保険だった場合/.test(d.what)), kenpo);
  }
  assert.ok(!api.deadlines({kenpo: 'company', ins: 'yes'}, D('2026-11-30')).some(d => /だった場合/.test(d.what)));
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
test('期限：月末・うるう年・年をまたぐ日付（14日）', () => {
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
const all = p => [p.today, ...p.next];
test('お金が足りない：今日やることは自立相談支援機関への電話。健康保険は選ぶ、年金は別に出る', () => {
  const p = api.plan({why: 'self', money: 'short', kenpo: 'company', ins: 'yes'}, D('2026-11-10'), D('2026-11-18'));
  assert.match(p.today, /自立相談支援/);
  assert.ok(p.next.some(x => /国民健康保険・任意継続・家族の扶養から選ぶ/.test(x)));
  assert.ok(p.next.some(x => /年金の手続き/.test(x) && /配偶者の扶養/.test(x)));
});
test('会社の健康保険：国保への切り替えを決めつけず、3つから選ぶことが先。会社都合なら軽減と免除も出る', () => {
  const p = api.plan({why: 'company', kenpo: 'company', ins: 'yes', money: 'ok'}, D('2026-11-10'), D('2026-11-18'));
  assert.match(p.today, /国民健康保険・任意継続・家族の扶養から選ぶ/);
  assert.ok(!all(p).some(x => /国民健康保険と国民年金の切り替えをする/.test(x)));
  assert.ok(p.next.some(x => /軽減/.test(x)));
  assert.ok(p.next.some(x => /免除/.test(x)));
  assert.ok(p.next.some(x => /ハローワーク/.test(x)));
});
test('外部点検の例：「しばらくは暮らせる」だけ選んだとき、加入先を決めつけず「確かめる」', () => {
  const p = api.plan({why: '', ins: '', kenpo: '', money: 'ok'}, null, D('2026-11-18'));
  assert.match(p.today, /確かめる/);
  assert.ok(!all(p).some(x => /切り替えをする|切り替える/.test(x)));
  const u = api.plan({why: 'self', ins: 'yes', kenpo: 'unknown', money: 'ok'}, null, D('2026-11-18'));
  assert.match(u.today, /確かめる/);
});
test('もともと国保・家族の扶養でも、お金が足りないなら年金の免除の案内は残す', () => {
  for (const kenpo of ['kokuho', 'family']) {
    const p = api.plan({why: 'self', kenpo, ins: 'yes', money: 'short'}, D('2026-11-10'), D('2026-11-18'));
    assert.ok(all(p).some(x => /免除/.test(x)), kenpo);
    assert.ok(!all(p).some(x => /健康保険を、国民健康保険/.test(x)), kenpo);
  }
});
test('年金の免除は14日の期限とは別と書く', () => {
  const p = api.plan({why: 'company', kenpo: 'company', ins: 'yes', money: 'short'}, D('2026-10-01'), D('2026-11-18'));
  assert.ok(all(p).some(x => /免除/.test(x) && /14日の期限とは別/.test(x)));
});
test('任意継続の期限（翌営業日）を過ぎたら、任意継続を選択肢に出さない', () => {
  // 2026-10-26 退職：任意継続の期限は 11-16（月）。11-16 当日はまだ選べる、11-17 は過ぎている
  assert.match(api.plan({kenpo: 'company'}, D('2026-10-26'), D('2026-11-16')).today, /国民健康保険・任意継続・家族の扶養から選ぶ/);
  const p = api.plan({kenpo: 'company'}, D('2026-10-26'), D('2026-11-17'));
  assert.match(p.today, /国民健康保険か家族の扶養から選ぶ/);
  assert.match(p.today, /20日は過ぎています/);
});
test('14日を過ぎたら「過ぎていても早く」と言う', () => {
  const p = api.plan({why: 'self', kenpo: 'company', ins: 'yes'}, D('2026-10-01'), D('2026-11-18'));
  assert.match(p.today, /14日を過ぎていても/);
});
test('雇用保険なし：職業訓練の案内に、テキスト代の自己負担と給付金の条件付きが入る', () => {
  const p = api.plan({why: 'self', kenpo: 'kokuho', ins: 'no'}, D('2026-11-10'), D('2026-11-18'));
  assert.ok(all(p).some(x => /求職者支援制度/.test(x) && /テキスト代は自己負担/.test(x) && /条件に合えば/.test(x)));
});
test('雇用保険なし：求職者支援制度を案内し、もともと国保なら役所の切り替えは出さない', () => {
  const p = api.plan({why: 'self', kenpo: 'kokuho', ins: 'no'}, D('2026-11-10'), D('2026-11-18'));
  assert.match(p.today, /求職者支援制度/);
  assert.ok(![p.today, ...p.next].some(x => /切り替え|健康保険を、/.test(x)));
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
test('本文：外部点検（2026-11-19）で直した文が残っている', () => {
  assert.match(html, /年金の手続きと、保険料の免除（切り替えは14日以内）/);
  assert.match(html, /期限は14日とは別/);
  assert.match(html, /2年1か月前まで/);
  assert.match(html, /世帯主と配偶者の所得も審査されます/);
  assert.match(html, /テキスト代は自己負担/);
  assert.match(html, /配偶者の勤め先を通して/);
  assert.match(html, /高年齢求職者給付金/);
  assert.doesNotMatch(html, /前年の所得にかかわらず/);
  assert.doesNotMatch(html, /今日から2週間/);
  assert.doesNotMatch(html, /やむを得ない欠席を含めて/);
  assert.doesNotMatch(html, /訓練は無料で受けられます/);
});
// ---- 公開版レビュー（2026-11-19）の指摘 1・3・4・5・6・7 ----
test('任意継続：確かでない期限（年末年始）は、一覧で「確かめて」と言い、今日やることでも任意継続を外さない', () => {
  // レビューの再現例：2026-12-09 退職、評価日 2026-12-30。20日目 12-29 は年末年始
  const d = nini('2026-12-09');
  assert.equal(d.sure, false);
  const p = api.plan({kenpo: 'company'}, D('2026-12-09'), D('2026-12-30'));
  assert.match(p.today, /国民健康保険・任意継続・家族の扶養から選ぶ/);
  assert.match(p.today, /確かめる/);
  assert.doesNotMatch(p.today, /20日は過ぎています/);
  // 祝日の表がない年（2028年）も同じ
  const q = api.plan({kenpo: 'company'}, D('2028-03-01'), D('2028-04-01'));
  assert.doesNotMatch(q.today, /20日は過ぎています/);
  // 一覧と今日やることは同じ判定を使う
  assert.equal(api.niniDeadline(D('2026-12-09')).sure, d.sure);
});
test('国保の軽減：自分から辞めた・分からない人にも、やむを得ない事情の軽減の確認を残す', () => {
  for (const why of ['self', 'unknown', '']) {
    const p = api.plan({why, kenpo: 'company', ins: 'yes', money: 'ok'}, D('2026-11-10'), D('2026-11-18'));
    assert.ok(all(p).some(x => /軽くなる場合/.test(x) && /病気/.test(x)), why);
  }
  assert.match(html, /<summary>解雇・雇止め・病気などで辞めた人は、国民健康保険料が大きく軽くなる場合があります<\/summary>/);
});
test('今日やることの各行から、相談先を探すページと言う文へ直接進める', () => {
  const p = api.plan({why: 'self', money: 'short', kenpo: 'company', ins: 'yes'}, D('2026-11-10'), D('2026-11-18'));
  assert.equal(p.keys.length, 1 + p.next.length);
  assert.match(api.links(p.keys[0], true), /minna-tunagaru\.jp\/ichiran/);
  assert.match(api.links(p.keys[0], true), /href="#say_jiritsu_d"/);
  const hw = p.keys[[p.today, ...p.next].findIndex(x => /ハローワークで求職の申込み/.test(x))];
  assert.match(api.links(hw), /koyou\/hellowork\.html/);
  assert.match(api.links(hw), /#say_hw_d/);
  for (const id of ['say_jiritsu_d', 'say_hw_d', 'say_kaisha_d', 'say_yakusho_d', 'kyushoku', 'hatarakenai']) assert.match(html, new RegExp(`id="${id}"`), id);
});
test('離職票が届かないときは会社だけで止めずハローワークへ。受付時間に16時前を添える', () => {
  const p = api.plan({why: 'self', kenpo: 'kokuho', ins: 'yes'}, D('2026-11-10'), D('2026-11-18'));
  assert.ok(all(p).some(x => /会社にいつ届くか聞き/.test(x) && /ハローワークに相談する/.test(x) && /16時前/.test(x)));
  assert.match(html, /祝日・年末年始を除く）の8時30分〜17時15分で、16時前/);
});
test('職業訓練の出席：育児・介護中や基礎コースの人の例外を書く。すぐ働けないときの延長と傷病手当金も書く', () => {
  assert.match(html, /育児・介護をしている人と、基礎コースを受ける人は、証明できないときも8割以上/);
  assert.match(html, /受給期間の延長/);
  assert.match(html, /傷病手当金/);
  assert.match(html, /2年を過ぎていても相談してください/);
  assert.doesNotMatch(html, /払えないまま放っておかず/);
});
