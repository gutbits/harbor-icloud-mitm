"""Scrape iCloud home (light) feature/marketing images and save for Harbor."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "home-light"
MANIFEST = OUT_DIR / "manifest.json"
ICLOUD_HOME = "https://www.icloud.com/"


def safe_name(url: str, index: int) -> str:
    path = urlparse(url).path
    base = path.split("/")[-1] if path else f"image-{index}"
    base = re.sub(r"[^\w.\-@]+", "_", base)
    if not base or base == "_":
        base = f"icloud-home-{index}.png"
    return base


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    collected: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(color_scheme="light", viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(ICLOUD_HOME, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(8000)
        try:
            page.wait_for_selector("main img, .feature-panel img, video", timeout=30_000)
        except Exception:
            pass

        # Hero video URLs (already in repo; record for manifest)
        video_src = page.evaluate(
            """() => {
              const v = document.querySelector('video');
              if (!v) return null;
              return v.currentSrc || v.src || v.querySelector('source')?.src || null;
            }"""
        )

        imgs = page.evaluate(
            """() => {
              const urls = new Set();
              document.querySelectorAll('img[src]').forEach((img) => {
                const s = img.currentSrc || img.src;
                if (s && !s.startsWith('data:')) urls.add(s);
              });
              document.querySelectorAll('img[srcset]').forEach((img) => {
                (img.srcset || '').split(',').forEach((part) => {
                  const u = part.trim().split(/\\s+/)[0];
                  if (u && !u.startsWith('data:')) urls.add(u);
                });
              });
              return Array.from(urls);
            }"""
        )

        # Prefer images from marketing / feature areas
        feature_imgs = page.evaluate(
            """() => {
              const sel = [
                '.feature-panel img',
                '.home-marketing img',
                'main img',
                '[class*="feature"] img',
                '[class*="marketing"] img',
              ].join(',');
              const urls = [];
              document.querySelectorAll(sel).forEach((img) => {
                const s = img.currentSrc || img.src;
                if (s) urls.push({ url: s, alt: img.alt || '', w: img.naturalWidth, h: img.naturalHeight });
              });
              return urls;
            }"""
        )

        browser.close()

    all_urls = list(dict.fromkeys(imgs))
    print(f"Found {len(all_urls)} img URLs total")
    for i, url in enumerate(all_urls):
        abs_url = urljoin(ICLOUD_HOME, url)
        name = safe_name(abs_url, i)
        dest = OUT_DIR / name
        try:
            download(abs_url, dest)
            collected.append({"url": abs_url, "file": name, "bytes": dest.stat().st_size})
            print(f"  OK {name} ({dest.stat().st_size} bytes)")
        except Exception as e:
            print(f"  FAIL {abs_url}: {e}")

    if video_src:
        vurl = urljoin(ICLOUD_HOME, video_src)
        vdest = OUT_DIR / "fpo@1x.mp4"
        try:
            download(vurl, vdest)
            collected.append({"url": vurl, "file": "fpo@1x.mp4", "type": "video"})
            print(f"  OK video fpo@1x.mp4")
        except Exception as e:
            print(f"  FAIL video: {e}")

    MANIFEST.write_text(
        json.dumps({"feature_imgs": feature_imgs, "downloaded": collected}, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {MANIFEST}")


if __name__ == "__main__":
    main()
