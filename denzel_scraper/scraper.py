#!/usr/bin/env python3
import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_DOMAIN = "https://www.denzel.at"


@dataclass
class ScrapeConfig:
    base_url: str
    output: Path
    delay_seconds: float
    timeout_seconds: int
    max_pages: Optional[int]
    max_vehicles: Optional[int]
    include_details: bool
    verbose: bool


class DenzelScraper:
    def __init__(self, config: ScrapeConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            }
        )

    def log(self, message: str) -> None:
        if self.config.verbose:
            print(message, file=sys.stderr)

    @staticmethod
    def clean_text(value: Optional[str]) -> str:
        if not value:
            return ""
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def parse_int_like(value: str) -> Optional[int]:
        if not value:
            return None
        digits = re.sub(r"[^0-9]", "", value)
        if not digits:
            return None
        return int(digits)

    def build_page_url(self, page_index: int) -> str:
        parts = urlsplit(self.config.base_url)
        query = parse_qs(parts.query, keep_blank_values=True)
        query["page"] = [str(page_index)]
        new_query = urlencode(query, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))

    def fetch(self, url: str, retries: int = 3) -> BeautifulSoup:
        last_error: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(url, timeout=self.config.timeout_seconds)
                response.raise_for_status()
                return BeautifulSoup(response.text, "html.parser")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.log(f"WARN fetch failed ({attempt}/{retries}): {url} -> {exc}")
                time.sleep(min(2.0 * attempt, 6.0))
        raise RuntimeError(f"Konnte URL nicht laden: {url}") from last_error

    def absolute_url(self, maybe_relative: str) -> str:
        return urljoin(BASE_DOMAIN, maybe_relative)

    def extract_total_results(self, soup: BeautifulSoup) -> Optional[int]:
        node = soup.select_one("#result-counter-number")
        if not node:
            return None
        return self.parse_int_like(self.clean_text(node.get_text(" ", strip=True)))

    def extract_last_page_index(self, soup: BeautifulSoup) -> int:
        page_indices: List[int] = []
        for a in soup.select("nav.pager a[data-page]"):
            data_page = a.get("data-page", "").strip()
            if data_page.isdigit():
                page_indices.append(int(data_page))
        if not page_indices:
            return 0
        return max(page_indices)

    def parse_listing_page(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        vehicles: List[Dict[str, Any]] = []
        cards = soup.select("#result-rows article.row")

        for card in cards:
            model_anchor = card.select_one(".modell a")
            detail_anchor = card.select_one("a.fzg-btn") or model_anchor
            if not detail_anchor:
                continue

            detail_href = detail_anchor.get("href", "").strip()
            if not detail_href:
                continue

            detail_url = self.absolute_url(detail_href)
            vehicle_id = None
            id_match = re.search(r"-(\d+)$", detail_href)
            if id_match:
                vehicle_id = id_match.group(1)

            brand = self.clean_text((card.select_one(".modell .brand") or {}).get_text(" ", strip=True) if card.select_one(".modell .brand") else "")
            title_raw = self.clean_text(model_anchor.get_text(" ", strip=True)) if model_anchor else ""
            model_name = title_raw
            if brand:
                model_name = re.sub(rf"^{re.escape(brand)}\s*-\s*", "", title_raw).strip()

            img = card.select_one(".img img")
            image_url = ""
            if img:
                image_url = img.get("data-src") or img.get("src") or ""
                image_url = self.absolute_url(image_url) if image_url else ""

            registration = self.clean_text((card.select_one("li.reg") or {}).get_text(" ", strip=True) if card.select_one("li.reg") else "")
            mileage = self.clean_text((card.select_one("li.km") or {}).get_text(" ", strip=True) if card.select_one("li.km") else "")
            price_node = card.select_one("li.price")
            price_text = self.clean_text(price_node.get_text(" ", strip=True)) if price_node else ""
            previous_price = ""
            if price_node:
                small = price_node.select_one("small")
                if small:
                    previous_price = self.clean_text(small.get_text(" ", strip=True))

            down_payment_node = card.select_one("ul.main li.font-weight-bold")
            down_payment = self.clean_text(down_payment_node.get_text(" ", strip=True)) if down_payment_node else ""
            tech_items = [self.clean_text(li.get_text(" ", strip=True)) for li in card.select("ul.tech li") if self.clean_text(li.get_text(" ", strip=True))]
            location = self.clean_text((card.select_one(".bottom .location") or {}).get_text(" ", strip=True) if card.select_one(".bottom .location") else "")

            vehicles.append(
                {
                    "vehicle_id": vehicle_id,
                    "title": title_raw,
                    "brand": brand,
                    "model": model_name,
                    "detail_url": detail_url,
                    "image_url": image_url,
                    "registration": registration,
                    "mileage": mileage,
                    "price_text": price_text,
                    "previous_price_text": previous_price,
                    "down_payment_text": down_payment,
                    "tech_specs": tech_items,
                    "location": location,
                }
            )

        return vehicles

    def parse_contacts(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        contacts: List[Dict[str, Any]] = []
        for slide in soup.select(".d2-sellers .swiper-slide"):
            name = self.clean_text((slide.select_one("h4") or {}).get_text(" ", strip=True) if slide.select_one("h4") else "")
            image_url = ""
            img = slide.select_one("img")
            if img:
                img_src = img.get("src") or img.get("data-src") or ""
                image_url = self.absolute_url(img_src) if img_src else ""

            email = ""
            phones: List[str] = []
            for a in slide.select("a[href]"):
                href = a.get("href", "")
                text = self.clean_text(a.get_text(" ", strip=True))
                if href.startswith("mailto:"):
                    email = href.replace("mailto:", "").strip() or text
                elif href.startswith("tel:") and text:
                    phones.append(text)

            if name or email or phones:
                contacts.append(
                    {
                        "name": name,
                        "email": email,
                        "phones": phones,
                        "image_url": image_url,
                    }
                )

        return contacts

    def parse_technical_data(self, soup: BeautifulSoup) -> Dict[str, str]:
        tech: Dict[str, str] = {}
        for item in soup.select("div[data-tab-id='technische-daten'] dl.d2-vehicle-data > div"):
            dt = item.select_one("dt")
            dd = item.select_one("dd")
            if not dt or not dd:
                continue
            key = self.clean_text(dt.get_text(" ", strip=True)).rstrip(":")
            value = self.clean_text(dd.get_text(" ", strip=True))
            if key:
                tech[key] = value
        return tech

    def parse_highlight_details(self, soup: BeautifulSoup) -> Dict[str, str]:
        highlights: Dict[str, str] = {}
        for box in soup.select(".d2-vehicle-details > div"):
            h = box.select_one("h3")
            s = box.select_one("span")
            if not h or not s:
                continue
            key = self.clean_text(h.get_text(" ", strip=True))
            value = self.clean_text(s.get_text(" ", strip=True))
            if key:
                highlights[key] = value
        return highlights

    def parse_location_block(self, soup: BeautifulSoup) -> Dict[str, str]:
        block: Dict[str, str] = {"name": "", "address": "", "phone": "", "image_url": ""}
        location_heading = None
        for h2 in soup.select("h2"):
            if self.clean_text(h2.get_text(" ", strip=True)).lower() == "standort":
                location_heading = h2
                break
        if not location_heading:
            return block

        section = location_heading.find_parent()
        if not section:
            return block

        name_node = section.select_one("h3")
        if name_node:
            block["name"] = self.clean_text(name_node.get_text(" ", strip=True))

        address_node = section.select_one("p")
        if address_node:
            block["address"] = self.clean_text(address_node.get_text(" ", strip=True))

        phone_node = section.select_one("a[href^='tel:']")
        if phone_node:
            block["phone"] = self.clean_text(phone_node.get_text(" ", strip=True))

        img = section.select_one("img")
        if img:
            src = img.get("src") or img.get("data-src") or ""
            if src:
                block["image_url"] = self.absolute_url(src)

        return block

    def parse_gallery_images(self, soup: BeautifulSoup) -> List[str]:
        urls: List[str] = []
        seen = set()
        for img in soup.select("main img[src*='/fahrzeuge/'], main img[data-src*='/fahrzeuge/']"):
            src = img.get("src") or img.get("data-src") or ""
            if not src:
                continue
            full = self.absolute_url(src)
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    def parse_similar_vehicles(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        similar: List[Dict[str, Any]] = []
        for card in soup.select("#block-views-block-gw-aehnliche-fahrzeuge-block-1 .card.fzg"):
            anchor = card.select_one("a[href]")
            if not anchor:
                continue
            href = anchor.get("href", "")
            detail_url = self.absolute_url(href) if href else ""

            img = card.select_one("img")
            image_url = ""
            if img:
                src = img.get("data-src") or img.get("src") or ""
                image_url = self.absolute_url(src) if src else ""

            price = self.clean_text((card.select_one(".fzg-price") or {}).get_text(" ", strip=True) if card.select_one(".fzg-price") else "")
            brand = self.clean_text((card.select_one(".fzg-brand") or {}).get_text(" ", strip=True) if card.select_one(".fzg-brand") else "")
            model = self.clean_text((card.select_one(".fzg-model") or {}).get_text(" ", strip=True) if card.select_one(".fzg-model") else "")
            description = self.clean_text((card.select_one(".fzg-description") or {}).get_text(" ", strip=True) if card.select_one(".fzg-description") else "")

            similar.append(
                {
                    "detail_url": detail_url,
                    "image_url": image_url,
                    "brand": brand,
                    "model": model,
                    "price_text": price,
                    "description": description,
                }
            )

        return similar

    def parse_detail_page(self, detail_url: str) -> Dict[str, Any]:
        soup = self.fetch(detail_url)

        title = self.clean_text((soup.select_one("main h1") or {}).get_text(" ", strip=True) if soup.select_one("main h1") else "")
        color = self.clean_text((soup.select_one("main h1 + span") or {}).get_text(" ", strip=True) if soup.select_one("main h1 + span") else "")

        current_price = self.clean_text((soup.select_one(".d2-price") or {}).get_text(" ", strip=True) if soup.select_one(".d2-price") else "")
        old_price = self.clean_text((soup.select_one(".tw-text-lg.tw-font-bold") or {}).get_text(" ", strip=True) if soup.select_one(".tw-text-lg.tw-font-bold") else "")
        new_price_line = self.clean_text((soup.find(string=re.compile(r"Neupreis", re.I)) or ""))

        description_node = soup.select_one("div[data-tab-id='beschreibung']")
        description = self.clean_text(description_node.get_text("\n", strip=True)) if description_node else ""

        equipment_node = soup.select_one("div[data-tab-id='ausstattung']")
        equipment = self.clean_text(equipment_node.get_text("\n", strip=True)) if equipment_node else ""

        details = {
            "title": title,
            "color": color,
            "detail_url": detail_url,
            "current_price_text": current_price,
            "old_price_text": old_price,
            "neupreis_text": new_price_line,
            "highlights": self.parse_highlight_details(soup),
            "contacts": self.parse_contacts(soup),
            "description": description,
            "technical_data": self.parse_technical_data(soup),
            "equipment": equipment,
            "location": self.parse_location_block(soup),
            "photo_links": self.parse_gallery_images(soup),
            "similar_vehicles": self.parse_similar_vehicles(soup),
        }
        return details

    def run(self) -> Dict[str, Any]:
        all_vehicles: List[Dict[str, Any]] = []
        seen_detail_urls = set()

        first_page_url = self.build_page_url(0)
        self.log(f"Lade Seite 0: {first_page_url}")
        first_soup = self.fetch(first_page_url)
        total_results = self.extract_total_results(first_soup)
        last_page_index = self.extract_last_page_index(first_soup)

        self.log(f"Gefundene Treffer (laut Seite): {total_results}")
        self.log(f"Letzte Seite (Index): {last_page_index}")

        page_index = 0
        while True:
            if self.config.max_pages is not None and page_index >= self.config.max_pages:
                self.log("Stoppe wegen --max-pages")
                break

            page_url = self.build_page_url(page_index)
            self.log(f"Lade Suchseite {page_index}: {page_url}")
            soup = first_soup if page_index == 0 else self.fetch(page_url)

            vehicles = self.parse_listing_page(soup)
            self.log(f"  Fahrzeuge auf Seite {page_index}: {len(vehicles)}")

            if not vehicles:
                self.log("Keine Fahrzeuge mehr gefunden, Stop.")
                break

            for vehicle in vehicles:
                detail_url = vehicle.get("detail_url", "")
                if detail_url in seen_detail_urls:
                    continue
                seen_detail_urls.add(detail_url)
                all_vehicles.append(vehicle)

                if self.config.max_vehicles is not None and len(all_vehicles) >= self.config.max_vehicles:
                    self.log("Stoppe wegen --max-vehicles")
                    break

            if self.config.max_vehicles is not None and len(all_vehicles) >= self.config.max_vehicles:
                break

            if page_index >= last_page_index:
                break

            page_index += 1
            time.sleep(self.config.delay_seconds)

        if self.config.include_details:
            for idx, vehicle in enumerate(all_vehicles, start=1):
                detail_url = vehicle.get("detail_url")
                if not detail_url:
                    continue
                self.log(f"Detail {idx}/{len(all_vehicles)}: {detail_url}")
                try:
                    vehicle["detail"] = self.parse_detail_page(detail_url)
                except Exception as exc:  # noqa: BLE001
                    vehicle["detail_error"] = str(exc)
                    self.log(f"WARN detail failed: {detail_url} -> {exc}")
                time.sleep(self.config.delay_seconds)

        scraped_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        output = {
            "source": self.config.base_url,
            "scraped_at_utc": scraped_at,
            "reported_total_results": total_results,
            "pages_processed": page_index + 1 if all_vehicles else 0,
            "vehicles_count": len(all_vehicles),
            "vehicles": all_vehicles,
        }
        return output


def parse_args() -> ScrapeConfig:
    parser = argparse.ArgumentParser(
        description="Scraper fuer Denzel Gebrauchtwagen-Suche inklusive Detailseiten und Bild-Links."
    )
    parser.add_argument(
        "--base-url",
        default="https://www.denzel.at/gebrauchtwagen/suche",
        help="Start-URL der Suche (Filter in Query erlaubt)",
    )
    parser.add_argument(
        "--output",
        default="denzel_scraper/output/denzel_vehicles.json",
        help="Pfad zur Ausgabe-JSON",
    )
    parser.add_argument("--delay", type=float, default=0.4, help="Wartezeit zwischen Requests in Sekunden")
    parser.add_argument("--timeout", type=int, default=25, help="HTTP Timeout in Sekunden")
    parser.add_argument("--max-pages", type=int, default=None, help="Optionales Limit fuer Suchseiten")
    parser.add_argument("--max-vehicles", type=int, default=None, help="Optionales Limit fuer Fahrzeuge")
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Nur Suchseite scrapen, keine Detailseiten",
    )
    parser.add_argument("--verbose", action="store_true", help="Mehr Logs auf stderr")

    args = parser.parse_args()

    return ScrapeConfig(
        base_url=args.base_url,
        output=Path(args.output),
        delay_seconds=args.delay,
        timeout_seconds=args.timeout,
        max_pages=args.max_pages,
        max_vehicles=args.max_vehicles,
        include_details=not args.no_details,
        verbose=args.verbose,
    )


def main() -> int:
    config = parse_args()
    scraper = DenzelScraper(config)

    data = scraper.run()

    config.output.parent.mkdir(parents=True, exist_ok=True)
    config.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Fertig: {data['vehicles_count']} Fahrzeuge gespeichert in {config.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
