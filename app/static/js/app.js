/* Pécule front-end glue (no build step).

   - Display preferences (theme, "€ / %", "Effet de change", "Par actif / Par
     compte") live in an Alpine
     store, are mirrored on <html data-...> for the CSS, and are saved in
     localStorage.
   - `allocation` draws the donut with Chart.js.
   - `lotForm` drives the purchase form.
   - Column sorting and error toasts are plain DOM code.

   This file must load before Alpine: it registers everything on "alpine:init". */
(function () {
  "use strict";

  const root = document.documentElement;

  function savePreference(key, value) {
    try {
      localStorage.setItem("pecule." + key, value);
    } catch (error) {
      /* storage unavailable (private browsing): the choice lasts until reload */
    }
  }

  /* ---- French number helpers ---- */

  // "1 234,56" -> 1234.56. Returns null for an empty or invalid text.
  function parseFrenchNumber(text) {
    const cleaned = String(text).replace(/[\s  ]/g, "").replace(",", ".");
    if (cleaned === "") return null;
    const value = Number(cleaned);
    return Number.isFinite(value) ? value : null;
  }

  const CURRENCY_SYMBOLS = { EUR: "€", USD: "$", GBP: "£", JPY: "¥" };
  const twoDecimals = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Same output as the server-side `money` filter: "1 234,56 €", "500,00 $", "12,00 CHF".
  function formatMoney(value, currency) {
    return twoDecimals.format(value) + "\u00a0" + (CURRENCY_SYMBOLS[currency] || currency);
  }

  /* ---- French date helpers (jj/mm/aaaa) ---- */

  // Keep digits only and insert the slashes while typing: "15032024" -> "15/03/2024".
  function maskDate(text) {
    const digits = String(text).replace(/\D/g, "").slice(0, 8);
    const parts = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4, 8)];
    return parts.filter((part) => part !== "").join("/");
  }

  function isoToFrenchDate(iso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    return match ? match[3] + "/" + match[2] + "/" + match[1] : "";
  }

  function frenchToIsoDate(text) {
    const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text);
    return match ? match[3] + "-" + match[2] + "-" + match[1] : "";
  }

  function formatPercent(ratio) {
    const options = { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 };
    return new Intl.NumberFormat("fr-FR", options).format(ratio);
  }

  /* ---- Sorting of the positions table ---- */

  // Kept outside the DOM so that the choice survives an HTMX refresh of the table.
  const sortState = { key: "value", direction: "desc" };

  function sortValue(position, key) {
    if (key === "name") return position.dataset.name;
    if (key === "pl") {
      return Number(root.dataset.plMode === "pct" ? position.dataset.plPct : position.dataset.plEur);
    }
    return Number(position.dataset[key]);
  }

  // Each row is one <tbody class="position"> (main row + its lot rows).
  function sorted(positions) {
    const sign = sortState.direction === "asc" ? 1 : -1;
    return Array.from(positions).sort(function (a, b) {
      const x = sortValue(a, sortState.key);
      const y = sortValue(b, sortState.key);
      return sign * (typeof x === "string" ? x.localeCompare(y, "fr") : x - y);
    });
  }

  function applySort() {
    const byAsset = document.getElementById("positions-table");
    if (byAsset) {
      sorted(byAsset.querySelectorAll("tbody.position")).forEach((row) => byAsset.appendChild(row));
      markSortedColumn(byAsset);
    }

    // "Par compte": rows are sorted inside their envelope; envelopes keep their order.
    const byEnvelope = document.getElementById("positions-by-envelope");
    if (byEnvelope) {
      byEnvelope.querySelectorAll("tbody.group-head").forEach(function (head) {
        const group = '[data-group="' + head.dataset.group + '"]';
        const rows = sorted(byEnvelope.querySelectorAll("tbody.position" + group));
        const total = byEnvelope.querySelector("tbody.group-total" + group);
        byEnvelope.appendChild(head);
        rows.forEach((row) => byEnvelope.appendChild(row));
        byEnvelope.appendChild(total);
      });
      markSortedColumn(byEnvelope);
    }
  }

  function markSortedColumn(table) {
    table.querySelectorAll("thead th").forEach(function (header) {
      const button = header.querySelector("[data-sort]");
      if (button && button.dataset.sort === sortState.key) {
        header.setAttribute("aria-sort", sortState.direction === "asc" ? "ascending" : "descending");
      } else {
        header.removeAttribute("aria-sort");
      }
    });
  }

  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-sort]");
    if (!button) return;
    const key = button.dataset.sort;
    if (sortState.key === key) {
      sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
    } else {
      sortState.key = key;
      sortState.direction = key === "name" ? "asc" : "desc";
    }
    applySort();
  });

  // A refreshed table arrives sorted by value: put the user's order back.
  document.addEventListener("htmx:afterSwap", applySort);

  /* ---- Error toast for failed HTMX requests ---- */

  let toastTimer = null;

  function showToast(message) {
    const toast = document.getElementById("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 6000);
  }

  document.addEventListener("htmx:responseError", function () {
    showToast("Le serveur a renvoyé une erreur. Réessayez dans un instant.");
  });
  document.addEventListener("htmx:sendError", function () {
    showToast("Serveur injoignable. Vérifiez que l'application est toujours lancée.");
  });

  /* ---- Alpine store and components ---- */

  document.addEventListener("alpine:init", function () {
    Alpine.store("ui", {
      theme: root.dataset.theme || "dark",
      plMode: root.dataset.plMode || "eur",
      fxEffect: root.dataset.fxEffect === "on",
      positionsView: root.dataset.positionsView || "asset",
      // asset id -> true when its lot rows are expanded. In memory only:
      // rows are collapsed again after a full page load.
      openAssets: {},

      toggleTheme() {
        this.theme = this.theme === "dark" ? "light" : "dark";
        root.dataset.theme = this.theme;
        savePreference("theme", this.theme);
        // The donut reads its colours from CSS variables: it must redraw.
        document.dispatchEvent(new CustomEvent("pecule:theme-changed"));
      },

      setPlMode(mode) {
        this.plMode = mode;
        root.dataset.plMode = mode;
        savePreference("plMode", mode);
        if (sortState.key === "pl") applySort();
      },

      setFxEffect(enabled) {
        this.fxEffect = enabled;
        root.dataset.fxEffect = enabled ? "on" : "off";
        savePreference("fxEffect", enabled ? "on" : "off");
      },

      setPositionsView(view) {
        this.positionsView = view;
        root.dataset.positionsView = view;
        savePreference("positionsView", view);
      },

      isOpen(assetId) {
        return this.openAssets[assetId] === true;
      },

      toggleAsset(assetId) {
        this.openAssets[assetId] = !this.isOpen(assetId);
      },
    });

    Alpine.data("allocation", function () {
      // Deliberately not part of the reactive data below: Alpine wraps its
      // data in proxies, and a proxied Chart.js instance misbehaves.
      let chart = null;
      let allSlices = {};
      let onThemeChanged = null;

      return {
        tab: "asset",

        init() {
          allSlices = JSON.parse(this.$refs.data.textContent);
          onThemeChanged = () => this.draw();
          document.addEventListener("pecule:theme-changed", onThemeChanged);
          this.draw();
        },

        // Called by Alpine when HTMX replaces the dashboard.
        destroy() {
          document.removeEventListener("pecule:theme-changed", onThemeChanged);
          if (chart) chart.destroy();
          chart = null;
        },

        show(tab) {
          this.tab = tab;
          this.draw();
        },

        // Hovering a legend row highlights the matching slice.
        highlight(index) {
          if (!chart) return;
          chart.setActiveElements(index === null ? [] : [{ datasetIndex: 0, index: index }]);
          chart.update();
        },

        draw() {
          if (typeof Chart === "undefined") return; // CDN unreachable: the legend table remains
          const slices = allSlices[this.tab] || [];
          const styles = getComputedStyle(root);
          const color = (name) => styles.getPropertyValue(name).trim();
          const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

          if (chart) chart.destroy();
          chart = new Chart(this.$refs.canvas, {
            type: "doughnut",
            data: {
              labels: slices.map((slice) => slice.label),
              datasets: [{
                data: slices.map((slice) => slice.value_eur),
                backgroundColor: slices.map((slice) => color("--series-" + slice.color_slot)),
                // A 2px gap in the card colour separates the slices (no outline).
                borderColor: color("--surface"),
                hoverBorderColor: color("--surface"),
                borderWidth: 2,
                hoverOffset: 6,
              }],
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              cutout: "70%",
              layout: { padding: 6 },
              animation: reducedMotion ? false : { duration: 350 },
              plugins: {
                legend: { display: false }, // the HTML table next to the chart is the legend
                tooltip: {
                  backgroundColor: color("--surface-raised"),
                  borderColor: color("--border-strong"),
                  borderWidth: 1,
                  titleColor: color("--text"),
                  bodyColor: color("--text-2"),
                  padding: 10,
                  boxWidth: 10,
                  boxHeight: 10,
                  boxPadding: 4,
                  callbacks: {
                    // The value leads, the name follows.
                    title: (items) => {
                      const slice = slices[items[0].dataIndex];
                      return formatMoney(slice.value_eur, "EUR") + " · " + formatPercent(slice.share);
                    },
                    label: (item) => item.label,
                  },
                },
              },
            },
          });
        },
      };
    });

    // Quoted units that are a hundredth of a currency -> that currency.
    const MINOR_UNITS = { GBX: "GBP", GBp: "GBP", ZAc: "ZAR", ILA: "ILS" };

    Alpine.data("lotForm", function (initial) {
      return {
        isin: initial.isin,
        name: initial.name,
        assetType: initial.assetType,
        ticker: initial.ticker,
        exchange: initial.exchange,
        currency: initial.currency,
        manualPrice: initial.manualPrice,
        tradeDate: initial.tradeDate,
        quantity: initial.quantity,
        unitPrice: initial.unitPrice,
        fees: initial.fees,
        fxRate: initial.fxRate,
        fxSource: initial.fxSource,
        isEdit: initial.isEdit,
        knownAssets: initial.knownAssets,

        init() {
          // A rate belongs to one currency: always fetch a new one when it changes.
          this.$watch("currency", () => {
            if (this.isForeign) this.reloadFxRate();
          });
          // A new date gets a new reference rate, unless the user typed their own.
          this.$watch("tradeDate", () => {
            const dateIsComplete = frenchToIsoDate(this.tradeDate) !== "";
            const typedByUser = this.fxSource === "manual" && String(this.fxRate).trim() !== "";
            if (this.isForeign && dateIsComplete && !typedByUser) this.reloadFxRate();
          });
        },

        // Assets already known for the ISIN being typed (same ISIN, any exchange).
        get matchingAssets() {
          const isin = this.isin.replace(/\s/g, "").toUpperCase();
          return this.knownAssets.filter((asset) => asset.isin === isin);
        },

        get isMinorUnit() {
          const code = this.currency.trim();
          return code in MINOR_UNITS || code.toUpperCase() in MINOR_UNITS;
        },

        // Currency in which the unit price and the fees are typed (GBP for a GBX listing).
        get priceCurrency() {
          const code = this.currency.trim();
          return MINOR_UNITS[code] || MINOR_UNITS[code.toUpperCase()] || code.toUpperCase();
        },

        get isForeign() {
          return /^[A-Z]{3}$/.test(this.priceCurrency) && this.priceCurrency !== "EUR";
        },

        // quantity x unit price + fees, or null while the form is incomplete.
        get costLocal() {
          const quantity = parseFrenchNumber(this.quantity);
          const unitPrice = parseFrenchNumber(this.unitPrice);
          if (quantity === null || unitPrice === null) return null;
          return quantity * unitPrice + (parseFrenchNumber(this.fees) || 0);
        },

        // The rate is "foreign units per 1 EUR": converting to EUR divides by it.
        get costEur() {
          const rate = parseFrenchNumber(this.fxRate);
          if (this.costLocal === null || rate === null || rate <= 0) return null;
          return this.costLocal / rate;
        },

        // Called by the buttons of partials/listing_candidates.html.
        pickListing(listing) {
          this.ticker = listing.ticker;
          this.exchange = listing.exchange;
          this.assetType = listing.asset_type;
          this.manualPrice = false;
          if (listing.name) this.name = listing.name;
          if (listing.currency) this.currency = listing.currency;
        },

        // Reuse an asset that is already in the portfolio.
        pickAsset(asset) {
          this.name = asset.name;
          this.assetType = asset.asset_type;
          this.ticker = asset.ticker;
          this.exchange = asset.exchange;
          this.currency = asset.currency;
          this.manualPrice = asset.manual_price === "on";
        },

        // Ask the server for the reference rate of (currency, purchase date).
        // $nextTick: let Alpine write the new values into the inputs first,
        // because HTMX reads them from the DOM.
        reloadFxRate() {
          this.$nextTick(() => htmx.trigger("#fx-slot", "fx-refresh"));
        },

        // Open the browser's own date picker, positioned on the hidden date input.
        openCalendar() {
          const calendar = this.$refs.calendar;
          calendar.value = frenchToIsoDate(this.tradeDate);
          if (typeof calendar.showPicker === "function") calendar.showPicker();
        },

        formatMoney: formatMoney,
        maskDate: maskDate,
        isoToFrenchDate: isoToFrenchDate,
      };
    });
  });
})();
