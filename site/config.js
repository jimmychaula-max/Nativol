/* Public website settings. Add only addresses and links you own and have checked.
 * Empty settings deliberately keep donation and unpublished download links inactive.
 * See docs/DONATIONS.md. This file must never contain a private key or seed phrase.
 */
window.NATIVOL_SITE = Object.freeze({
  version: "0.5.0-beta.1",
  downloadURL: "https://github.com/jimmychaula-max/Nativol/releases/download/v0.5.0-beta.1/Nativol-0.5.0-beta.1-Intel-Beta.dmg",
  releaseURL: "https://github.com/jimmychaula-max/Nativol/releases/tag/v0.5.0-beta.1",
  sourceURL: "https://github.com/jimmychaula-max/Nativol",
  buyMeACoffeeURL: "https://buymeacoffee.com/nativol",
  binancePay: Object.freeze({
    recipient: "Jimnicklaus",
    binanceID: "74331910",
    qrImage: "assets/binance-pay-receive.jpg"
  }),
  wallets: Object.freeze([
    Object.freeze({
      id: "btc-bitcoin",
      label: "Bitcoin",
      asset: "BTC",
      network: "Bitcoin (BTC)",
      address: "12YC52dDN8bZn8TD2Cui4dCW9SvMHTYaZT"
    }),
    Object.freeze({
      id: "usdt-bnb-smart-chain",
      label: "USDT on BNB Smart Chain",
      asset: "USDT",
      network: "BNB Smart Chain (BEP20)",
      address: "0xcbc2d9c7a95c86b5bc0822810ec5cc9b38e8aa62"
    }),
    Object.freeze({
      id: "usdt-tron",
      label: "USDT on Tron",
      asset: "USDT",
      network: "Tron (TRC20)",
      address: "TGUd3GoYUbGxwuAwFLEHJcTMUatsEKnUBg"
    }),
    Object.freeze({
      id: "usdt-ethereum",
      label: "USDT on Ethereum",
      asset: "USDT",
      network: "Ethereum (ERC20)",
      address: "0xcbc2d9c7a95c86b5bc0822810ec5cc9b38e8aa62"
    })
  ])
});
