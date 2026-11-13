/* 自動更新が止まっていたら、画面の上で知らせる（古い情報を新しいと思わせないため）。外部への通信はしない。 */
(function () {
  var b = document.body;
  var gen = Date.parse(b.getAttribute("data-generated"));
  var hours = parseFloat(b.getAttribute("data-stale-hours") || "72");
  if (!gen || isNaN(gen)) return;
  var age = (Date.now() - gen) / 3600000;
  if (age > hours) {
    var el = document.getElementById("stale");
    if (!el) return;
    var days = Math.floor(age / 24);
    el.textContent = "このページは" + days + "日以上、自動で更新されていません。並んでいる情報が古い可能性があります。最新の情報は、各機関の公式ページで確かめてください。";
    el.hidden = false;
  }
})();
