#!/usr/bin/env node
/*
 * 見た目の点検（docs/design-standard.md の「自動で測る項目」）。
 *
 * 使い方：
 *   python scripts/build.py --out site
 *   node scripts/design_audit.cjs site            # 全ページ
 *   node scripts/design_audit.cjs site shitsugyo/  # 1ページだけ
 *
 * Playwright（Chromium）が要る。Claude の作業コンテナには入っている。GitHub Actions では動かしていない
 * （ブラウザの導入が重いため）。ページを直したら、PR を出す前に手元かコンテナで実行し、結果を PR に貼る。
 *
 * 測ること（スマホ幅 390px）：
 *  1. 文字の大きさ：決まった段（14・16・17・18・20・22・26・28・36px）以外を使っていないか。14px 未満がないか
 *  2. 角の丸み：4・8・12px・円（50%）・札（999px）以外を使っていないか
 *  3. 押せる部品：高さ 24px 未満がないか（WCAG 2.2 の 2.5.8。文中のリンクは除く）
 *  4. 見出し・選択肢の改行：word-break: auto-phrase が効いているか
 *  5. 最後の行が2字以下で終わる段落（泣き別れ）の数（参考値。0 を目指すが、合否にはしない）
 *  6. 横にはみ出していないか（WCAG 1.4.10 リフロー。320px でも測る。320px で文字を1.25倍にしたときも測る：
 *     html の文字の大きさを 20px にして、ブラウザの文字の拡大を模す）
 *  7. 看板の「メニュー」を開いたときの 1〜3 と 6（390px、320px・文字1.25倍）
 */
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const root = path.resolve(process.argv[2] || 'site');
let pages = process.argv.slice(3);
if (!pages.length) pages = ['', 'shitsugyo/', 'yachin/', 'sasaeru/', 'sasaeru/oya-yachin/', 'sasaeru/shitsugyo-yachin/',
  'sasaeru/taikyo/', 'sasaeru/denwa/', 'sasaeru/seikatsuhogo/', 'unei.html', 'about.html'];

const SIZES = new Set([14, 16, 17, 18, 20, 22, 26, 28, 36]);
const RADII = new Set(['0px', '4px', '8px', '12px', '50%', '999px']);
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' };

const srv = http.createServer((q, r) => {
  let p = decodeURIComponent(q.url.split('?')[0]);
  if (p.endsWith('/')) p += 'index.html';
  const f = path.join(root, p);
  if (!f.startsWith(root)) { r.statusCode = 403; return r.end(); }
  fs.readFile(f, (e, d) => {
    if (e) { r.statusCode = 404; return r.end(); }
    r.setHeader('content-type', TYPES[path.extname(f)] || 'application/octet-stream');
    r.end(d);
  });
});

srv.listen(0, async () => {
  const port = srv.address().port;
  const browser = await chromium.launch();
  let failed = 0;
  for (const p of pages) {
    const problems = [];
    let info = {};
    for (const [width, zoom, open] of [[390, 0], [320, 0], [320, 20], [390, 0, true], [320, 20, true]]) {
      const page = await browser.newPage({ viewport: { width, height: 844 } });
      await page.goto(`http://localhost:${port}/${p}`);
      if (zoom) await page.addStyleTag({ content: `html { font-size: ${zoom}px !important; }` });
      await page.waitForTimeout(150);
      if (open) { const b = await page.$('[data-site-menu]'); if (!b) { await page.close(); continue; } await b.click(); await page.waitForTimeout(100); }
      const r = await page.evaluate(([sizes, radii]) => {
        const out = { badSize: new Set(), badRadius: new Set(), smallTargets: [], noPhrase: [], orphans: 0, overflow: 0 };
        for (const el of document.querySelectorAll('body *')) {
          const s = getComputedStyle(el);
          if (s.display === 'none' || s.visibility === 'hidden') continue;
          const box = el.getBoundingClientRect();
          if (!box.width && !box.height) continue;
          const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
          if (hasText) {
            const fz = Math.round(parseFloat(s.fontSize) * 100) / 100;
            if (!sizes.includes(fz)) out.badSize.add(`${fz}px「${el.textContent.trim().slice(0, 12)}」`);
          }
          if (s.borderTopLeftRadius !== '0px' && !radii.includes(s.borderTopLeftRadius)) out.badRadius.add(`${s.borderTopLeftRadius}（${el.tagName.toLowerCase()}.${el.className}）`);
          if (/^(A|BUTTON|SUMMARY|SELECT|INPUT)$/.test(el.tagName) && el.type !== 'hidden') {
            const inSentence = s.display === 'inline' && el.closest('p, li, dd, td');
            const covered = el.tagName === 'INPUT' && (el.type === 'radio' || el.type === 'checkbox') && s.opacity === '0';
            if (!inSentence && !covered && box.height < 24) out.smallTargets.push(`${Math.round(box.height)}px「${(el.textContent || el.value || '').trim().slice(0, 12)}」`);
          }
          if (/^(H1|H2|H3|LEGEND)$/.test(el.tagName) && s.wordBreak !== 'auto-phrase') out.noPhrase.push(el.textContent.trim().slice(0, 16));
          if (box.right > document.documentElement.clientWidth + 1 && !el.closest('.tablewrap, pre, .sign-path, .hero')) out.overflow++;
        }
        for (const el of document.querySelectorAll('p, li, h1, h2, h3, legend')) {
          const rg = document.createRange(); rg.selectNodeContents(el);
          const lines = {};
          for (const rc of rg.getClientRects()) { const k = Math.round(rc.top); lines[k] = (lines[k] || 0) + rc.width; }
          const ks = Object.keys(lines).map(Number).sort((a, b) => a - b);
          if (ks.length > 1 && lines[ks[ks.length - 1]] < parseFloat(getComputedStyle(el).fontSize) * 2.6) out.orphans++;
        }
        return { badSize: [...out.badSize], badRadius: [...out.badRadius], smallTargets: out.smallTargets, noPhrase: out.noPhrase, orphans: out.orphans, overflow: out.overflow };
      }, [[...SIZES], [...RADII]]);
      await page.close();
      if (width === 390 && open) {
        if (r.badSize.length) problems.push(`メニューを開いたとき、決まった段以外の文字の大きさ：${r.badSize.slice(0, 8).join('、')}`);
        if (r.badRadius.length) problems.push(`メニューを開いたとき、決まり以外の角の丸み：${r.badRadius.slice(0, 6).join('、')}`);
        if (r.smallTargets.length) problems.push(`メニューを開いたとき、高さ24px未満の押せる部品：${r.smallTargets.slice(0, 6).join('、')}`);
      } else if (width === 390) {
        info = r;
        if (r.badSize.length) problems.push(`決まった段以外の文字の大きさ：${r.badSize.slice(0, 8).join('、')}`);
        if (r.badRadius.length) problems.push(`決まり以外の角の丸み：${r.badRadius.slice(0, 6).join('、')}`);
        if (r.smallTargets.length) problems.push(`高さ24px未満の押せる部品：${r.smallTargets.slice(0, 6).join('、')}`);
        if (r.noPhrase.length) problems.push(`文節で改行しない見出し：${r.noPhrase.slice(0, 6).join('、')}`);
      }
      if (r.overflow) problems.push(`${width}px 幅${zoom ? '（文字1.25倍）' : ''}${open ? '・メニューを開いたとき' : ''}で横にはみ出す要素：${r.overflow}個`);
    }
    const name = p || '（トップ）';
    if (problems.length) { failed++; console.log(`✗ ${name}`); problems.forEach(x => console.log(`   - ${x}`)); }
    else console.log(`✓ ${name}`);
    console.log(`   参考：最後の行が2字以下の段落・見出し ${info.orphans}個`);
  }
  await browser.close();
  srv.close();
  process.exit(failed ? 1 : 0);
});
