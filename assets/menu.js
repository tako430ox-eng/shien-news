// サイトのメニュー（看板の右上の「メニュー」ボタン）。外部への通信はしない。
// ボタンは HTML では hidden。スクリプトが動いたときだけ出す（動かないときは、ページの下の案内から移れる）。
(function () {
  var btn = document.querySelector("[data-site-menu]");
  if (!btn) return;
  var nav = document.getElementById(btn.getAttribute("aria-controls"));
  if (!nav) return;
  var label = btn.querySelector(".menu-label");
  function isOpen() { return btn.getAttribute("aria-expanded") === "true"; }
  function set(open, focusBtn) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    nav.hidden = !open;
    if (label) label.textContent = open ? "閉じる" : "メニュー";
    if (!open && focusBtn) btn.focus();
  }
  btn.hidden = false;
  btn.addEventListener("click", function () { set(!isOpen()); });
  document.addEventListener("keydown", function (ev) {
    if ((ev.key === "Escape" || ev.key === "Esc") && isOpen()) set(false, true);
  });
  // 開いたまま Tab で本文へ進んだら、本文のフォーカスはそのままにして閉じる。
  // 開いた一覧が本文を押し下げたり覆ったりして、選んだ部品が見えなくならないように（WCAG 2.2 の 2.4.11）
  document.addEventListener("focusin", function (ev) {
    var t = ev.target;
    if (!isOpen() || t === btn || nav.contains(t)) return;
    set(false);
    if (t.scrollIntoView) t.scrollIntoView({ block: "nearest" });
  });
  // 同じページの中へ移るリンク（相談先の一覧など）を押したら閉じる
  nav.addEventListener("click", function (ev) { if (ev.target.closest && ev.target.closest("a")) set(false); });
  // 「戻る」でこのページに戻ったとき、開いたままにしない
  window.addEventListener("pageshow", function () { set(false); });
})();
