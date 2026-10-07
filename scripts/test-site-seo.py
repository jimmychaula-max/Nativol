#!/usr/bin/env python3
"""Check public-page discovery, metadata, and the JSON-LD/CSP contract offline."""

import base64
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ORIGIN = "https://nativol.org"
ROUTES = {"/": "index.html", "/guide": "guide.html"}


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.ids = set()
        self.scripts = []
        self.script = None
        self.feed(path.read_text())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if "id" in attrs:
            assert attrs["id"] not in self.ids, f"Duplicate id: {attrs['id']}"
            self.ids.add(attrs["id"])
        if tag == "script":
            self.script = {**attrs, "body": ""}
            self.scripts.append(self.script)

    def handle_endtag(self, tag):
        if tag == "script":
            self.script = None

    def handle_data(self, data):
        if self.script is not None:
            self.script["body"] += data

    def select(self, tag, **attrs):
        return [values for name, values in self.tags if name == tag
                and all(values.get(key) == value for key, value in attrs.items())]

    def meta(self, key):
        values = [attrs["content"] for tag, attrs in self.tags if tag == "meta"
                  and key in (attrs.get("name"), attrs.get("property"))]
        assert len(values) == 1, f"Expected one {key} metadata value"
        return values[0]


pages = {route: Page(SITE / name) for route, name in ROUTES.items()}
csp = (SITE / "_headers").read_text()
assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
assert "script-src 'self'" in csp and "object-src 'none'" in csp
hashes = set()
graphs = {}
for route, page in pages.items():
    assert len(page.select("title")) == 1 and len(page.select("h1")) == 1
    canonical = page.select("link", rel="canonical")
    assert len(canonical) == 1 and canonical[0]["href"] == ORIGIN + route
    assert page.meta("og:url") == ORIGIN + route
    assert page.meta("description") == page.meta("og:description") == page.meta("twitter:description")
    assert page.meta("og:title") == page.meta("twitter:title")
    assert "Intel" in page.meta("description") and "15.7.9" in page.meta("description")
    assert not any("noindex" in attrs.get("content", "").lower()
                   for attrs in page.select("meta", name="robots"))
    blocks = [script for script in page.scripts if script.get("type") == "application/ld+json"]
    assert len(blocks) == 1
    for script in page.scripts:
        if not script.get("src"):
            assert script.get("type") == "application/ld+json", "Unexpected executable inline script"
    block = blocks[0]["body"]
    graph = json.loads(block)
    assert graph["@context"] == "https://schema.org"
    graphs[route] = graph
    digest = "sha256-" + base64.b64encode(hashlib.sha256(block.encode()).digest()).decode()
    hashes.add(digest)
    assert "'" + digest + "'" in csp, f"Update the JSON-LD CSP hash for {route}: {digest}"
    for tag, attrs in page.tags:
        if tag not in {"a", "link", "script", "img"}:
            continue
        source = attrs.get("href") if tag in {"a", "link"} else attrs.get("src")
        if not source:
            continue
        target = urlsplit(urljoin(ORIGIN + route, source))
        if target.netloc != "nativol.org":
            continue
        assert target.path not in {"/index.html", "/guide.html"}, f"Noncanonical internal link: {source}"
        path = ROUTES.get(target.path, unquote(target.path).lstrip("/"))
        assert (SITE / path).is_file(), f"Missing internal destination: {source}"
        if target.fragment and target.path in pages:
            assert unquote(target.fragment) in pages[target.path].ids, f"Missing fragment: {source}"
assert set(re.findall(r"'((?:sha256|sha384|sha512)-[^']+)'", csp)) == hashes

entities = {entry["@type"]: entry for entry in graphs["/"]["@graph"]}
assert entities["WebSite"]["url"] == ORIGIN + "/"
assert entities["WebSite"]["name"] == "Nativol"
app = entities["SoftwareApplication"]
config = (SITE / "config.js").read_text()
for schema_key, config_key in [("softwareVersion", "version"), ("downloadUrl", "downloadURL"), ("releaseNotes", "releaseURL")]:
    assert app[schema_key] == re.search(r'\b' + config_key + r':\s*"([^"]+)"', config).group(1)
assert app["isAccessibleForFree"] is True and app["offers"]["price"] == 0
assert app["operatingSystem"] == "macOS 15.7.9" and "Intel" in app["processorRequirements"]
assert "macFUSE 5.4.0" in app["softwareRequirements"]
assert "beta" in app["softwareVersion"] and "not notarized" in app["description"]
assert "aggregateRating" not in app and "review" not in app, "Only add supported public reviews"
breadcrumbs = graphs["/guide"]["itemListElement"]
assert graphs["/guide"]["@type"] == "BreadcrumbList"
assert [item["position"] for item in breadcrumbs] == [1, 2]
assert [item["item"] for item in breadcrumbs] == [ORIGIN + "/", ORIGIN + "/guide"]

ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
sitemap = ET.parse(SITE / "sitemap.xml")
locations = [node.text for node in sitemap.findall("s:url/s:loc", ns)]
assert len(locations) == len(set(locations)) and set(locations) == {ORIGIN + route for route in ROUTES}
for node in sitemap.findall("s:url/s:lastmod", ns):
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", node.text)
robots = (SITE / "robots.txt").read_text()
assert "User-agent: *" in robots and "Sitemap: " + ORIGIN + "/sitemap.xml" in robots
assert not re.search(r"^Disallow:\s*/\s*$", robots, re.M | re.I)
assert "noindex" in Page(SITE / "404.html").meta("robots")
print("Website SEO checks passed: two canonical pages, internal links/assets, sitemap, JSON-LD release facts, CSP hashes, and 404 noindex.")
