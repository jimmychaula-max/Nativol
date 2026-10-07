(function (root, factory) {
  "use strict";
  var api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NativolSite = api;
  if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", function () { api.init(root.document, root.NATIVOL_SITE); }, { once: true });
    } else api.init(root.document, root.NATIVOL_SITE);
  }
})(typeof window === "undefined" ? globalThis : window, function (root) {
  "use strict";

  function plainText(value, maximum) {
    if (typeof value !== "string") return "";
    var text = value.trim();
    if (!text || text.length > maximum || /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/.test(text)) return "";
    return text;
  }

  function safeHTTPSURL(value) {
    if (typeof value !== "string" || !value || value.length > 2048 || /\s/.test(value)) return "";
    try {
      var url = new URL(value);
      if (url.protocol !== "https:" || !url.hostname || url.username || url.password) return "";
      return url.href;
    } catch (_) { return ""; }
  }

  function validateCoffeeURL(value) {
    var safe = safeHTTPSURL(value);
    if (!safe) return "";
    var url = new URL(safe);
    if ((url.hostname !== "buymeacoffee.com" && url.hostname !== "www.buymeacoffee.com") || url.port || url.search || url.hash) return "";
    if (!/^\/[A-Za-z0-9][A-Za-z0-9_-]{0,99}\/?$/.test(url.pathname)) return "";
    return safe;
  }

  function validateWallet(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    var id = plainText(value.id, 48);
    var label = plainText(value.label, 60);
    var asset = plainText(value.asset, 24);
    var network = plainText(value.network, 64);
    var address = plainText(value.address, 256);
    if (!/^[a-z][a-z0-9-]*$/.test(id) || !label || !asset || !network || !address) return null;
    // Format bounds only: this does not verify ownership, network, or a blockchain checksum.
    // Public addresses are copied literally, never interpreted as links or payment requests.
    if (!/^[A-Za-z0-9._:-]+$/.test(address) || address !== value.address) return null;
    return Object.freeze({ id: id, label: label, asset: asset, network: network, address: address });
  }

  function validateWallets(values) {
    if (!Array.isArray(values)) return [];
    var wallets = [];
    var used = new Set();
    values.slice(0, 12).forEach(function (value) {
      var wallet = validateWallet(value);
      if (wallet && !used.has(wallet.id)) {
        used.add(wallet.id);
        wallets.push(wallet);
      }
    });
    return wallets;
  }

  function validateBinancePay(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    var recipient = plainText(value.recipient, 60);
    var binanceID = plainText(value.binanceID, 32);
    // This is a Binance account identifier, never an on-chain receiving address.
    // Only the reviewed, locally stored Receive QR is allowed; no remote QR service.
    if (!recipient || !/^[0-9]+$/.test(binanceID) || binanceID !== value.binanceID || value.qrImage !== "assets/binance-pay-receive.jpg") return null;
    return Object.freeze({ recipient: recipient, binanceID: binanceID, qrImage: value.qrImage });
  }

  function validateConfig(value) {
    var config = value && typeof value === "object" ? value : {};
    return Object.freeze({
      version: plainText(config.version, 40),
      downloadURL: safeHTTPSURL(config.downloadURL),
      releaseURL: safeHTTPSURL(config.releaseURL),
      sourceURL: safeHTTPSURL(config.sourceURL),
      buyMeACoffeeURL: validateCoffeeURL(config.buyMeACoffeeURL),
      binancePay: validateBinancePay(config.binancePay),
      wallets: Object.freeze(validateWallets(config.wallets))
    });
  }

  async function attemptCopy(address, clipboard) {
    if (!clipboard || typeof clipboard.writeText !== "function") return false;
    try {
      await clipboard.writeText(address);
      return true;
    } catch (_) { return false; }
  }

  function selectAddress(doc, node) {
    try {
      node.focus();
      var selection = doc.defaultView.getSelection();
      if (!selection) return false;
      var range = doc.createRange();
      range.selectNodeContents(node);
      selection.removeAllRanges();
      selection.addRange(range);
      return selection.toString() === node.textContent;
    } catch (_) { return false; }
  }

  function element(doc, tag, className, text) {
    var node = doc.createElement(tag);
    node.className = className;
    if (typeof text === "string") node.textContent = text;
    return node;
  }

  function walletCard(doc, wallet, status) {
    var article = element(doc, "article", "wallet-card");
    var heading = element(doc, "div", "wallet-card-heading");
    var title = element(doc, "h4", "wallet-label", wallet.label);
    title.id = "wallet-title-" + wallet.id;
    article.setAttribute("aria-labelledby", title.id);
    heading.appendChild(title);
    heading.appendChild(element(doc, "span", "wallet-asset", wallet.asset));
    article.appendChild(heading);
    article.appendChild(element(doc, "p", "wallet-network", "Network: " + wallet.network));
    var address = element(doc, "code", "wallet-address", wallet.address);
    address.tabIndex = 0;
    address.setAttribute("aria-label", wallet.asset + " address on " + wallet.network);
    article.appendChild(address);
    var button = element(doc, "button", "wallet-copy", "Copy address");
    button.type = "button";
    button.setAttribute("aria-label", "Copy " + wallet.asset + " address on " + wallet.network);
    button.addEventListener("click", async function () {
      button.textContent = "Copy address";
      button.disabled = true;
      var copied = await attemptCopy(wallet.address, root.navigator && root.navigator.clipboard);
      button.disabled = false;
      if (copied) {
        if (status) status.textContent = "Copied the " + wallet.asset + " address on " + wallet.network + ".";
        button.textContent = "Copied";
        root.setTimeout(function () { button.textContent = "Copy address"; }, 2200);
      } else {
        var selected = selectAddress(doc, address);
        if (status) status.textContent = selected
          ? "Automatic copying is unavailable. The full address is selected; copy it manually using your browser or keyboard."
          : "Automatic copying is unavailable. Select the full address above and copy it manually.";
      }
    });
    article.appendChild(button);
    return article;
  }

  function configureLink(doc, id, url) {
    var link = doc.getElementById(id);
    if (!link || !url) return;
    link.href = url;
    link.rel = "noopener noreferrer";
  }

  function binancePayCard(doc, pay, status) {
    var section = element(doc, "section", "binance-pay");
    section.setAttribute("aria-labelledby", "binance-pay-title");
    var image = element(doc, "img", "binance-pay-qr");
    image.src = pay.qrImage;
    image.alt = "Binance Pay Receive QR for " + pay.recipient;
    image.width = 180;
    image.height = 180;
    image.loading = "lazy";
    section.appendChild(image);
    var details = element(doc, "div", "binance-pay-details");
    var title = element(doc, "h4", "binance-pay-title", "Scan with Binance Pay");
    title.id = "binance-pay-title";
    details.appendChild(title);
    details.appendChild(element(doc, "p", "binance-pay-recipient", "Recipient: " + pay.recipient));
    details.appendChild(element(doc, "p", "binance-pay-id-label", "Binance ID"));
    var identifier = element(doc, "code", "wallet-address binance-pay-id", pay.binanceID);
    identifier.tabIndex = 0;
    identifier.setAttribute("aria-label", "Binance ID " + pay.binanceID);
    details.appendChild(identifier);
    var button = element(doc, "button", "wallet-copy", "Copy Binance ID");
    button.type = "button";
    button.addEventListener("click", async function () {
      button.textContent = "Copy Binance ID";
      button.disabled = true;
      var copied = await attemptCopy(pay.binanceID, root.navigator && root.navigator.clipboard);
      button.disabled = false;
      if (copied) {
        if (status) status.textContent = "Copied Binance ID " + pay.binanceID + ".";
        button.textContent = "Copied";
        root.setTimeout(function () { button.textContent = "Copy Binance ID"; }, 2200);
      } else {
        var selected = selectAddress(doc, identifier);
        if (status) status.textContent = selected
          ? "Automatic copying is unavailable. The full Binance ID is selected; copy it manually using your browser or keyboard."
          : "Automatic copying is unavailable. Select the full Binance ID above and copy it manually.";
      }
    });
    details.appendChild(button);
    section.appendChild(details);
    section.appendChild(element(doc, "p", "binance-pay-note", "Open Binance Pay to scan. Confirm " + pay.recipient + " as the recipient in Binance before sending. This Binance ID is not a blockchain address."));
    return section;
  }

  function initDonations(doc, config) {
    var coffee = doc.getElementById("coffee-link");
    var coffeePending = doc.getElementById("coffee-pending");
    if (coffee) {
      coffee.hidden = !config.buyMeACoffeeURL;
      if (config.buyMeACoffeeURL) {
        coffee.href = config.buyMeACoffeeURL;
        coffee.rel = "noopener noreferrer";
      } else coffee.removeAttribute("href");
    }
    if (coffeePending) coffeePending.hidden = !!config.buyMeACoffeeURL;
    var container = doc.getElementById("wallets-container");
    var pending = doc.getElementById("crypto-pending");
    var status = doc.getElementById("donation-status");
    var binance = doc.getElementById("binance-pay-container");
    if (binance) {
      binance.replaceChildren();
      if (config.binancePay) binance.appendChild(binancePayCard(doc, config.binancePay, status));
    }
    if (container) {
      container.replaceChildren();
      config.wallets.forEach(function (wallet) { container.appendChild(walletCard(doc, wallet, status)); });
    }
    var hasWallets = !!(container && config.wallets.length);
    var onchainPanel = doc.getElementById("onchain-panel");
    if (onchainPanel) onchainPanel.hidden = !hasWallets;
    var networkNote = doc.getElementById("network-note");
    if (networkNote) networkNote.hidden = !hasWallets;
    var walletHeading = doc.getElementById("wallets-heading");
    if (walletHeading) walletHeading.hidden = !hasWallets;
    if (pending) pending.hidden = !!(hasWallets || (binance && config.binancePay));
  }

  function initDemoMenu(doc) {
    var toggle = doc.getElementById("menu-toggle");
    var menu = doc.getElementById("demo-menu");
    if (!toggle || !menu) return;
    toggle.hidden = false;
    var hint = doc.getElementById("menu-hint");
    if (hint) hint.hidden = false;
    toggle.setAttribute("aria-controls", "demo-menu");
    function setOpen(open) {
      toggle.setAttribute("aria-expanded", String(open));
      menu.hidden = !open;
    }
    setOpen(false);
    toggle.addEventListener("click", function () { setOpen(menu.hidden); });
    doc.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !menu.hidden) {
        setOpen(false);
        toggle.focus();
      }
    });
    doc.addEventListener("click", function (event) {
      if (!menu.hidden && !menu.contains(event.target) && !toggle.contains(event.target)) setOpen(false);
    });
  }

  function init(doc, rawConfig) {
    var config = validateConfig(rawConfig);
    configureLink(doc, "nav-download-link", config.downloadURL);
    configureLink(doc, "hero-download-link", config.downloadURL);
    configureLink(doc, "download-link", config.downloadURL);
    configureLink(doc, "release-link", config.releaseURL);
    configureLink(doc, "source-link", config.sourceURL);
    initDonations(doc, config);
    initDemoMenu(doc);
  }

  return Object.freeze({ safeHTTPSURL: safeHTTPSURL, validateCoffeeURL: validateCoffeeURL, validateWallet: validateWallet, validateWallets: validateWallets, validateBinancePay: validateBinancePay, validateConfig: validateConfig, attemptCopy: attemptCopy, init: init });
});
