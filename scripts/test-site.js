#!/usr/bin/env node
"use strict";
const assert = require("node:assert/strict");
const site = require("../site/site.js");
let assertions = 0;
function check(actual, expected) { assert.deepEqual(actual, expected); assertions += 1; }

async function main() {
  check(site.validateConfig({}).wallets, []);
  check(site.validateConfig(null).buyMeACoffeeURL, "");
  check(site.validateConfig({}).binancePay, null);
  for (const value of ["", "javascript:alert(1)", "http://example.com", "data:text/html,x", "https://user:secret@example.com", "https://example.com/ white", {}, null]) {
    check(site.safeHTTPSURL(value), "");
  }
  check(site.safeHTTPSURL("https://example.com/release"), "https://example.com/release");
  check(site.validateCoffeeURL("https://www.buymeacoffee.com/nativol-test"), "https://www.buymeacoffee.com/nativol-test");
  check(site.validateCoffeeURL("https://buymeacoffee.com/nativol_test/"), "https://buymeacoffee.com/nativol_test/");
  for (const value of ["https://buymeacoffee.com", "https://buymeacoffee.com.evil.example/person", "https://evil.example/buymeacoffee.com/person", "https://buymeacoffee.com/person/extra", "https://buymeacoffee.com/person?tracking=1", "https://buymeacoffee.com:444/person", "https://me@buymeacoffee.com/person", "https://buymeacoffee.com/%70erson", "https://buymeacoffee.com/person#fragment"]) {
    check(site.validateCoffeeURL(value), "");
  }
  // This is a non-address sentinel used only in tests, never a public receiving destination.
  const wallet = { id: "fixture", label: "Fixture", asset: "TEST", network: "Test fixture only", address: "NOT-A-REAL-WALLET" };
  check(site.validateWallet(wallet), wallet);
  check(site.validateWallets([wallet, wallet]).length, 1);
  check(site.validateWallets("wrong"), []);
  for (const change of [{ id: "bad id" }, { label: "" }, { asset: "" }, { network: "" }, { address: "" }, { address: " padded " }, { address: "<script>" }, { network: "bad\u202evalue" }, { address: "a".repeat(257) }, { id: "__proto__" }]) {
    check(site.validateWallet({ ...wallet, ...change }), null);
  }
  check(site.validateWallets(Array.from({ length: 20 }, (_, index) => ({ ...wallet, id: "fixture-" + index }))).length, 12);
  // A test-only account identifier, not a donation destination.
  const pay = { recipient: "Test recipient", binanceID: "00000000", qrImage: "assets/binance-pay-receive.jpg" };
  check(site.validateBinancePay(pay), pay);
  check(site.validateConfig({ binancePay: pay }).binancePay, pay);
  for (const value of [null, [], "wrong", { ...pay, recipient: "" }, { ...pay, recipient: "bad\u202evalue" }, { ...pay, binanceID: " 00000000 " }, { ...pay, binanceID: 12345678 }, { ...pay, binanceID: "0".repeat(33) }, { ...pay, binanceID: "javascript:1" }, { ...pay, qrImage: "https://example.com/qr.png" }, { ...pay, qrImage: "../private.jpg" }, { ...pay, qrImage: "assets/binance-pay-receive.jpg?remote=1" }]) {
    check(site.validateBinancePay(value), null);
  }
  let copied = "";
  check(await site.attemptCopy(wallet.address, { writeText: async value => { copied = value; } }), true);
  check(copied, wallet.address);
  check(await site.attemptCopy(wallet.address, { writeText: async () => { throw new Error("Permission denied"); } }), false);
  check(await site.attemptCopy(wallet.address, undefined), false);
  check(await site.attemptCopy(wallet.address, {}), false);
  check(await site.attemptCopy(pay.binanceID, { writeText: async value => { copied = value; } }), true);
  check(copied, pay.binanceID);
  console.log("Website configuration and clipboard checks passed (" + assertions + " assertions).");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
