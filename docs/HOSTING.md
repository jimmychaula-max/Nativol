# Website and download hosting

Nativol uses **Cloudflare Pages for the static website at [nativol.org](https://nativol.org)** and **GitHub Releases for the Intel beta DMG, checksums, and matching source archive**. The domain is registered with Cloudflare. The [0.5.0-beta.1 release](https://github.com/jimmychaula-max/Nativol/releases/tag/v0.5.0-beta.1) and public source repository are available. The custom domain is active with SSL enabled, and the live HTTPS site was verified on 7 October 2026.

The website is the small static `site/` directory. It needs no paid runtime, database, application server, or build framework. Apple Developer ID signing and notarization are separate from website hosting.

## Cloudflare Pages settings

The `nativol` Pages project uses Git integration with the public Nativol repository. Automatic production deployments are enabled from `main`:

| Setting | Value |
|---|---|
| Framework preset | None |
| Production branch | `main` |
| Root directory | Repository root |
| Build command | `exit 0` |
| Build output directory | `site` |

The project is available at [nativol.pages.dev](https://nativol.pages.dev), with **[nativol.org](https://nativol.org)** configured as its active custom domain. For any additional domain, add it through the project's Custom domains screen before modifying DNS and wait for its certificate to become active. Do not replace unrelated DNS records.

An active Cloudflare Single Redirect sends `www.nativol.org` to `https://nativol.org` with HTTP 301, preserving the path and query string. On 7 October 2026, `https://www.nativol.org/guide?source=redirect-check` was verified to land on the live guide at `https://nativol.org/guide?source=redirect-check`.

Official references, checked 7 October 2026: [static HTML deployment](https://developers.cloudflare.com/pages/framework-guides/deploy-anything/) and [custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/).

Direct Upload is an alternative for a separate project, not the mode used here. Deploy the contents of `site/`, with `index.html` at the deployment root. The Cloudflare-specific upload ZIP contains this layout; the older general website ZIP also includes documentation and must not be uploaded unchanged. Direct Upload projects cannot later switch to Git integration; use a new project or continue manual/CLI deployments. See [Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/).

The site declares `https://nativol.org/` as its canonical homepage. Cloudflare Pages redirects `.html` pages to extensionless paths, so the guide's canonical URL is `https://nativol.org/guide`. `robots.txt` points to the two-page sitemap. The `_headers` file permits local scripts, styles and images, disallows embedding and form submission, and disables camera, microphone, location and browser payment access. See [serving Pages](https://developers.cloudflare.com/pages/configuration/serving-pages/).

## GitHub source and releases

Keep source in the [Nativol repository](https://github.com/jimmychaula-max/Nativol) and downloads in a beta GitHub Release, including the DMG, SHA-256 checksum, and matching source archive. Keep the original third-party license notices and corresponding source. Do not put release binaries in the site's deployment directory.

The verified repository, beta release page, and direct DMG asset URLs are configured as `sourceURL`, `releaseURL`, and `downloadURL` in `site/config.js`. The main download buttons start the DMG download from GitHub; the separate release link provides checksums and matching source. Keep the HTML fallback links and guide download link aligned with the same published version when releasing an update, so downloading also works without JavaScript. Configure optional donations as described in [DONATIONS.md](DONATIONS.md). The Binance Pay receiving QR is public; Buy Me a Coffee remains inactive pending payout setup. `site/config.example.json` is an optional deployment record; the website reads `config.js`.

The existing `.github/workflows/pages.yml` is an optional GitHub Pages alternative. It has no push trigger and does not configure Cloudflare. Leave it unused when Cloudflare Pages is the website host.

## Verification and ongoing updates

The initial live deployment loaded over HTTPS with working JavaScript. The [GitHub source-check run](https://github.com/jimmychaula-max/Nativol/actions/runs/37619896956) passed, and remote release asset digests matched the reviewed immutable archives. These checks do not broaden the app's hardware validation or establish production readiness.

## Local preview and publication checks

```sh
python3 -m http.server 8080 --directory site
```

Open `http://localhost:8080`. The site uses relative links, system fonts, and local CSS and JavaScript. It has no analytics, cookies, remote fonts, embedded payment forms, or automatic third-party requests. Buy Me a Coffee opens only when selected. The Binance Pay QR is a local image; the site does not create payments or receive payment confirmations.

Before announcing a deployment:

1. Complete the applicable release checklist and sanitize published source and archives.
2. Verify the beta Release assets and checksums, then configure the actual source and release links.
3. Check the deployed homepage and guide over HTTPS, including the custom domain, canonical URLs, and sitemap.
4. Test donation links, full receiving details, address copying, mobile layout, and keyboard access. Confirm the Binance recipient in the Binance app before sending money.
5. Verify the version and compatibility copy. The app remains an Intel testing beta, with the same signing and hardware-validation limits after website publication.
