# Website search metadata

Nativol has two indexable pages with distinct purposes:

| Canonical URL | Search intent |
| --- | --- |
| `https://nativol.org/` | Free NTFS read/write software for Intel Macs; download the testing beta |
| `https://nativol.org/guide` | Install Nativol, macFUSE and the required permissions |

Titles, descriptions and social metadata identify the Intel beta. The supported writing configuration remains **native Intel macOS 15.7.9 with macFUSE 5.4.0**. Metadata must not imply Apple silicon, all macOS versions or production readiness.

## Discovery and URLs

Internal page links, canonical tags, Open Graph URLs and the sitemap use the same HTTPS apex URLs. Cloudflare Pages redirects `/index.html` to `/` and `/guide.html` to `/guide`; the domain configuration redirects HTTP and `www` to HTTPS `nativol.org`. Unknown paths must keep a real 404 status. The 404 page also has `noindex` and is excluded from the sitemap.

`robots.txt` permits crawling and advertises the sitemap. `lastmod` records an actual page change, not each deployment. Do not change it for an unrelated release or routine redeploy. These choices follow Google's [canonical URL guidance](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls) and [sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).

## Search Console

The `nativol.org` domain property was verified on 7 October 2026. Google processed `https://nativol.org/sitemap.xml` successfully and discovered both public pages. Keep the DNS ownership-verification record in place. The property covers the domain's protocols and subdomains; the sitemap identifies the preferred HTTPS apex URLs.

Search Console data and search indexing are asynchronous. A successful sitemap submission or indexing request does not mean a page has already entered Google's index.

## Structured data

The homepage contains `WebSite` for the Nativol site name and `SoftwareApplication` for the app, with the exact beta version, download, platform requirements and a zero-price offer. The guide contains `BreadcrumbList` for the home-to-guide hierarchy. These describe existing public content; they do not invent ratings, reviews, customers or certifications.

The app data is valid descriptive Schema.org markup but **does not qualify for Google's software-app rich result** because that feature requires a genuine review or aggregate rating. Do not fabricate one to clear a validator warning. Google also decides whether supported features appear. See its [software-app requirements](https://developers.google.com/search/docs/appearance/structured-data/software-app), [site-name guidance](https://developers.google.com/search/docs/appearance/site-names), [breadcrumb guidance](https://developers.google.com/search/docs/appearance/structured-data/breadcrumb) and [structured-data policies](https://developers.google.com/search/docs/appearance/structured-data/sd-policies).

The JSON-LD is present in the original HTML. Exact SHA-256 hashes are included in `_headers`; executable inline scripts and `unsafe-inline` remain disallowed. If a JSON-LD block changes, update its CSP hash from the exact bytes between its script tags. The validator prints the required replacement hash when it finds a mismatch.

## Validate and publish

Run `python3 scripts/test-site-seo.py` and `node scripts/test-site.js` before publishing. The SEO check covers canonical links and fragments, local assets, sitemap consistency, structured-data release values and CSP hashes. It does not simulate Google indexing or measure Core Web Vitals.

After deployment, check public responses for `/`, `/guide`, the sitemap, robots file, redirect aliases and a missing path. Use Search Console URL Inspection for both canonical pages and submit `https://nativol.org/sitemap.xml`; request indexing after material changes. Review Google's [Rich Results Test](https://search.google.com/test/rich-results) for the guide and use [Schema.org Validator](https://validator.schema.org/) for the descriptive app data. Submission and structured data do not guarantee indexing, ranking or rich-result display.

The simple `python3 -m http.server` preview serves `/guide.html` directly but does not reproduce Cloudflare Pages' extensionless `/guide` route. Check canonical navigation on a Pages preview or production deployment.
