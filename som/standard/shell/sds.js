/* SDS v1 runtime. No dependencies, no network, degrades to a static document
 * if it never runs.
 *
 * Filtering reads ONLY the per-row `data-f` JSON array that the emitter wrote.
 * It never scans cell text. That is deliberate: scanning text makes a search
 * for "1100" match a PO number, an item description, and a date, which is the
 * over-matching bug this design already fixed once.
 */
(function () {
  "use strict";

  function rowsOf(table) {
    return Array.prototype.slice.call(table.tBodies[0] ? table.tBodies[0].rows : []);
  }

  function filterValues(row) {
    var raw = row.getAttribute("data-f");
    if (!raw) return [];
    try { return JSON.parse(raw); } catch (e) { return []; }
  }

  function wireTable(wrap) {
    var table = wrap.querySelector("table.sds-table");
    if (!table || !table.tBodies.length) return;

    var id = wrap.getAttribute("data-table-id") || "";
    var bar = document.querySelector('.sds-tablebar[data-for="' + id + '"]');
    var search = bar && bar.querySelector(".sds-search");
    var toggle = bar && bar.querySelector('[data-act="cols"]');
    var count = bar && bar.querySelector(".sds-rowcount");
    var rows = rowsOf(table);
    var total = rows.length;

    function apply() {
      var q = (search && search.value || "").trim().toLowerCase();
      var shown = 0;
      for (var i = 0; i < rows.length; i++) {
        var hit = true;
        if (q) {
          hit = false;
          var vals = filterValues(rows[i]);
          for (var j = 0; j < vals.length; j++) {
            if (String(vals[j]).toLowerCase().indexOf(q) !== -1) { hit = true; break; }
          }
        }
        rows[i].hidden = !hit;
        if (hit) shown++;
      }
      if (count) {
        count.textContent = shown === total
          ? total.toLocaleString() + " rows"
          : shown.toLocaleString() + " / " + total.toLocaleString() + " rows";
      }
    }

    if (search) {
      search.addEventListener("input", apply);
      search.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { search.value = ""; apply(); }
      });
    }

    if (toggle) {
      toggle.addEventListener("click", function () {
        var all = wrap.getAttribute("data-cols") === "all";
        wrap.setAttribute("data-cols", all ? "core" : "all");
        toggle.setAttribute("aria-pressed", all ? "false" : "true");
        toggle.textContent = all ? toggle.getAttribute("data-label-core")
                                 : toggle.getAttribute("data-label-all");
      });
    }

    apply();
  }

  function wireLang() {
    var btns = document.querySelectorAll('[data-act="lang"]');
    if (!btns.length) return;
    Array.prototype.forEach.call(btns, function (b) {
      b.addEventListener("click", function () {
        var next = b.getAttribute("data-lang");
        document.documentElement.setAttribute("lang", next);
        Array.prototype.forEach.call(btns, function (x) {
          x.setAttribute("aria-pressed", String(x.getAttribute("data-lang") === next));
        });
        // Per-viewer convenience only. Never load-bearing.
        try { localStorage.setItem("sds.lang", next); } catch (e) { /* private mode */ }
      });
    });
    try {
      var saved = localStorage.getItem("sds.lang");
      if (saved) {
        var target = document.querySelector('[data-act="lang"][data-lang="' + saved + '"]');
        if (target) target.click();
      }
    } catch (e) { /* private mode, blocked site data, thumbnailer */ }
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll(".sds-tablewrap"), wireTable);
    wireLang();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
