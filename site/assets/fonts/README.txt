Nativol website fonts

Manrope variable font and IBM Plex Mono Regular / Medium.
Downloaded 2026-10-07 from the official Google Fonts repository:
https://github.com/google/fonts/tree/main/ofl/manrope
https://github.com/google/fonts/tree/main/ofl/ibmplexmono

These fonts are self-hosted; visitors make no requests to Google Fonts.
Both font families use the SIL Open Font License 1.1.
See Manrope-OFL.txt and IBMPlexMono-OFL.txt for their copyright and license notices.

Web formats
-----------
The original TTF files and license notices are retained unchanged. The WOFF2
files were generated locally on 2026-10-07 with fontTools 4.60.2 and Brotli 1.2.0.
They retain every glyph, character mapping, layout feature, and variable-font
axis; no subsetting, hint removal, or outline simplification was applied.
WOFF2 conversion omits IBM Plex Mono's empty DSIG table (zero signatures).

Source TTF                  Web WOFF2                    Bytes (TTF -> WOFF2)
Manrope[wght].ttf            Manrope-Variable.woff2       164700 -> 53892
IBMPlexMono-Regular.ttf      IBMPlexMono-Regular.woff2    135580 -> 39176
IBMPlexMono-Medium.ttf       IBMPlexMono-Medium.woff2     136704 -> 40080

Combined transfer size: 436984 -> 133148 bytes (69.5% smaller).
Manrope's weight axis remains 200 through 800. IBM Plex Mono's files provide
regular (400) and medium (500) weights.

Validation compared glyph order, all decomposed outlines, Unicode mappings,
horizontal metrics, name records, and remaining layout/variation tables against
the source TTFs. The transformed glyf/loca tables and head checksum/flags were
excluded from byte-level table comparisons; glyph outlines were checked directly.

Reproduction (fontTools with the woff extra installed):
    from fontTools.ttLib import TTFont
    font = TTFont(source_ttf, recalcTimestamp=False)
    font.flavor = "woff2"
    font.save(destination_woff2)

Source TTF SHA-256
3ae11c49db0455a3cc33e37d380f20fdb8c7f8b41dc07625c177e3d87a9d6ae6  Manrope[wght].ttf
6a3412f058c7d8dfd9170c41e85ade48e5156ecb89356110ca57a0a27734af46  IBMPlexMono-Regular.ttf
a9b4c49bb299e05b5f6c481e7fb5e78943d2793249a0c8874ab574a2d1ea6755  IBMPlexMono-Medium.ttf

WOFF2 SHA-256
30b83738add8c9edd9e3450b98036a9a8fb5668d0cbd4eb0ce5fe6761197f21f  Manrope-Variable.woff2
13b16cd770a73f60304510221c3e7186d396ba6ca5d64c52b5c6fb983d0e900a  IBMPlexMono-Regular.woff2
0498bcb48294756b5ac9062290e1639e907b1ec69b5dc2e8cfdec3fab58e0bc8  IBMPlexMono-Medium.woff2
