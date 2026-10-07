# Website design

The editable [Nativol website design in Figma](https://www.figma.com/design/oydHqrV9SQip4TNi58u2GL?node-id=3-2) includes desktop and mobile compositions, color variables, typography styles, reusable button states, and editorial row components. File access follows the owner's Figma permissions.

The implementation is plain HTML, CSS, and local JavaScript in `site/`. It uses Manrope for text and IBM Plex Mono for technical labels. Fonts are hosted locally; copyright and SIL Open Font License notices are in `site/assets/fonts/`. The drive artwork in `site/assets/ntfs-drive.svg` is an editable vector illustration, not a screenshot of the app.

## Updating the site

- Keep the exact writing configuration, beta status, dependency and signing limits visible. The website redesign does not broaden app support.
- Keep download, release and donation destinations in `site/config.js`. Preserve asset/network pairs and literal receiving addresses.
- Keep cryptocurrency options in the native disclosure with full readable addresses, copy feedback and keyboard access.
- Use the existing paper, ink, sage and forest palette, hairline separators and restrained button shapes. Avoid nested marketing cards and decorative motion.
- Check the landing page and installation guide at narrow mobile, tablet and desktop widths. Honor reduced motion.
- Publish only `site/` through the existing Cloudflare Pages integration. Application binaries remain on GitHub Releases.

Run `node scripts/test-site.js` for configuration and clipboard behavior checks. Preview with `python3 -m http.server 8768 --directory site` and inspect real browser rendering before deployment.
