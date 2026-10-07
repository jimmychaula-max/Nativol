# Optional donations on the website

Nativol is free. Supporting development is optional and does not unlock features or change the app's license.

The website has no payment form, wallet connection, tracking script, or remote QR service. A coffee button links to the maintainer's Buy Me a Coffee profile when configured. Binance Pay uses a locally stored Receive QR and a public Binance ID. Separate on-chain wallet cards display public receiving addresses and their explicitly named networks, with an optional local clipboard action. The website does not create transactions or receive payment confirmations.

## Configure the links

Edit `site/config.js`. It runs before `site/site.js` on the live [nativol.org](https://nativol.org) Cloudflare Pages site. The site includes the Binance Pay receiving QR and ID, plus the four asset/network options below. Buy Me a Coffee remains inactive pending payout setup. Unconfigured options show “coming soon” text instead of fake destinations.

| Setting | Value to provide |
| --- | --- |
| `buyMeACoffeeURL` | Your complete HTTPS profile URL on `buymeacoffee.com` or `www.buymeacoffee.com`, without query parameters. |
| `binancePay` | A verified receiving configuration using the fields below; `null` hides this option. |
| `wallets` | An array of wallet objects using the fields below. Leave it empty to hide wallet cards. |
| `downloadURL` | The published HTTPS DMG asset URL. Main download buttons use this URL. |
| `releaseURL` | The published HTTPS beta release page, with checksums and matching source. |
| `sourceURL` | The published HTTPS source repository. Empty keeps the page's existing information link. |
| `version` | The release version represented by the page. Update the visible HTML release copy when it changes. |

## Binance Pay

The published website contains receiving details observed in the signed-in Binance account on 7 October 2026:

| Field | Configured value |
| --- | --- |
| `recipient` | `Jimnicklaus` |
| `binanceID` | `74331910`, displayed under the exact label **Binance ID** in Binance. This is not a blockchain address. |
| `qrImage` | `assets/binance-pay-receive.jpg`, the captured Receive QR with no fixed amount or selected currency. Only this local path is accepted by the website. |

Apple Vision decoded the captured image as `https://app.binance.com/uni-qr/5nGMjeDj`. Opening that URL in a desktop browser redirected to the Binance app download page, so the site offers the QR and **Copy Binance ID**, without a web checkout link.

The on-page instruction asks supporters to scan in Binance Pay and confirm **Jimnicklaus** before sending. The ID copy button reports clipboard errors and offers manual selection instead of reporting a false success. Account IDs are kept separate from `wallets`; never enter a Binance ID as an on-chain receiving address.

The receiving QR and Binance ID are published. An independent scan in a sender's Binance app still needs to confirm the recipient; no test payment, successful donation, or donation-policy approval has been verified. Do not substitute an expiring payment request for this Receive QR. If the receiving details change, recapture the QR, confirm its payload and recipient, then replace the local image and configuration together.

## Buy Me a Coffee

The [Nativol profile](https://buymeacoffee.com/nativol) is live. Its website link was updated to `https://nativol.org` on 7 October 2026. The service still requires the owner to set up a payout method before receiving support. Keep `buyMeACoffeeURL` empty until that setup is complete and the public support flow has been checked.

## On-chain wallets

The maintainer supplied these public receiving addresses and Binance deposit screenshots on 7 October 2026. Asset/network labels follow those screenshots. The BNB Smart Chain and Ethereum entries intentionally use the same address but remain separate choices; the asset is **USDT** on both networks.

| Asset | Network | Receiving address |
| --- | --- | --- |
| BTC | Bitcoin (BTC) | `12YC52dDN8bZn8TD2Cui4dCW9SvMHTYaZT` |
| USDT | BNB Smart Chain (BEP20) | `0xcbc2d9c7a95c86b5bc0822810ec5cc9b38e8aa62` |
| USDT | Tron (TRC20) | `TGUd3GoYUbGxwuAwFLEHJcTMUatsEKnUBg` |
| USDT | Ethereum (ERC20) | `0xcbc2d9c7a95c86b5bc0822810ec5cc9b38e8aa62` |

These are receiving details supplied by the maintainer; no test transfer or successful deposit has been verified. The site displays and copies addresses without connecting a wallet or preparing a transaction.

Each wallet object requires all five fields:

| Field | Meaning |
| --- | --- |
| `id` | A unique lowercase identifier beginning with a letter; remaining characters may be letters, digits, or hyphens. Maximum 48 characters. |
| `label` | A readable title, maximum 60 characters. |
| `asset` | The asset name or symbol, maximum 24 characters. |
| `network` | The exact blockchain network accepted by this receiving address, maximum 64 characters. |
| `address` | The complete public receiving address, maximum 256 characters, with no whitespace. Supported characters are letters, digits, period, underscore, colon, and hyphen. |

Do not put private keys, recovery phrases, API tokens, or account credentials in this file. Everything in the website folder is public. This simple address-only interface is unsuitable for destinations requiring an additional memo or destination tag; leave those unconfigured until the interface supports them explicitly.

## Check before changing donation details

1. Copy receiving details from your own wallet and confirm the asset and network independently. The website performs format bounds only; it does not verify ownership, address checksum, or network compatibility.
2. Keep network names explicit, especially where the same address format appears on different blockchains.
3. Preview the site and check each full address visually. Click **Copy address**, paste it into a text editor, and compare the entire result with your original.
4. Test the coffee button and make sure it opens your intended profile.
5. Scan the Binance Pay QR in a sender's Binance app and verify the recipient. Check that **Copy Binance ID** produces the exact configured ID. This check need not send a payment.
6. Run `node scripts/test-site.js`. Hosting over HTTPS enables clipboard support in compatible browsers. If clipboard access is denied or unavailable, the page selects the full visible address or ID where possible and gives manual-copy instructions; it never reports a failed copy as successful.

The donation section remains present when an option is unconfigured, but no payment or receiving address is fabricated. Verify future changes locally before publishing them through the connected Git repository.
