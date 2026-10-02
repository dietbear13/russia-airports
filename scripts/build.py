"""Сборка справочника аэропортов России из исходников в sources/.

Запуск: python scripts/build.py [--check-site]
Результат: data/airports.csv, data/airports.json, data/terminals.csv.

--check-site заново проверяет, у каких аэропортов есть страница на
flight-delayed.ru, и обновляет sources/site_pages.json (1 запрос в секунду).
"""

import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from timezonefinder import TimezoneFinder

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "sources"
OUT = ROOT / "data"
SITE = "https://flight-delayed.ru"

# Военные аэродромы с IATA/ICAO-кодом, но без гражданских пассажирских рейсов.
MILITARY = {"UUMU", "UUEM", "UUMB", "ULLK", "UHKG", "UODS", "UODN"}
MILITARY_IATA = {"CKL", "KLD", "NOI", "TYA", "LNX", "UKS", "EIK"}

# Крым: в OurAirports числится под UA, на сайте и в справочнике — субъект РФ.
CRIMEA = {"UKFF": ("Республика Крым", "RU-CR")}

REGION_RU = {
    "RU-AD": "Республика Адыгея", "RU-AL": "Республика Алтай", "RU-ALT": "Алтайский край",
    "RU-AMU": "Амурская область", "RU-ARK": "Архангельская область", "RU-AST": "Астраханская область",
    "RU-BA": "Республика Башкортостан", "RU-BEL": "Белгородская область", "RU-BRY": "Брянская область",
    "RU-BU": "Республика Бурятия", "RU-CE": "Чеченская Республика", "RU-CHE": "Челябинская область",
    "RU-CHU": "Чукотский АО", "RU-CU": "Чувашская Республика", "RU-DA": "Республика Дагестан",
    "RU-IN": "Республика Ингушетия", "RU-IRK": "Иркутская область", "RU-IVA": "Ивановская область",
    "RU-KAM": "Камчатский край", "RU-KB": "Кабардино-Балкарская Республика", "RU-KDA": "Краснодарский край",
    "RU-KEM": "Кемеровская область", "RU-KGD": "Калининградская область", "RU-KGN": "Курганская область",
    "RU-KHA": "Хабаровский край", "RU-KHM": "Ханты-Мансийский АО", "RU-KIR": "Кировская область",
    "RU-KK": "Республика Хакасия", "RU-KL": "Республика Калмыкия", "RU-KLU": "Калужская область",
    "RU-KO": "Республика Коми", "RU-KOS": "Костромская область", "RU-KR": "Республика Карелия",
    "RU-KRS": "Курская область", "RU-KYA": "Красноярский край", "RU-LEN": "Ленинградская область",
    "RU-LIP": "Липецкая область", "RU-MAG": "Магаданская область", "RU-ME": "Республика Марий Эл",
    "RU-MO": "Республика Мордовия", "RU-MOS": "Московская область", "RU-MOW": "Москва",
    "RU-MUR": "Мурманская область", "RU-NEN": "Ненецкий АО", "RU-NGR": "Новгородская область",
    "RU-NIZ": "Нижегородская область", "RU-NVS": "Новосибирская область", "RU-OMS": "Омская область",
    "RU-ORE": "Оренбургская область", "RU-ORL": "Орловская область", "RU-PER": "Пермский край",
    "RU-PNZ": "Пензенская область", "RU-PRI": "Приморский край", "RU-PSK": "Псковская область",
    "RU-ROS": "Ростовская область", "RU-RYA": "Рязанская область", "RU-SA": "Республика Саха (Якутия)",
    "RU-SAK": "Сахалинская область", "RU-SAM": "Самарская область", "RU-SAR": "Саратовская область",
    "RU-SE": "Республика Северная Осетия — Алания", "RU-SMO": "Смоленская область",
    "RU-SPE": "Санкт-Петербург", "RU-STA": "Ставропольский край", "RU-SVE": "Свердловская область",
    "RU-TA": "Республика Татарстан", "RU-TAM": "Тамбовская область", "RU-TOM": "Томская область",
    "RU-TUL": "Тульская область", "RU-TVE": "Тверская область", "RU-TY": "Республика Тыва",
    "RU-TYU": "Тюменская область", "RU-UD": "Удмуртская Республика", "RU-ULY": "Ульяновская область",
    "RU-VGG": "Волгоградская область", "RU-VLA": "Владимирская область", "RU-VLG": "Вологодская область",
    "RU-VOR": "Воронежская область", "RU-YAN": "Ямало-Ненецкий АО", "RU-YAR": "Ярославская область",
    "RU-YEV": "Еврейская АО", "RU-ZAB": "Забайкальский край",
}

CYR = re.compile(r"[А-Яа-яЁё]")


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_overrides():
    """Ручные правки поверх источников: sources/overrides.csv (code,field,value,reason)."""
    path = SRC / "overrides.csv"
    out = {}
    if path.exists():
        for r in read_csv(path):
            out.setdefault(r["code"], {})[r["field"]] = r["value"]
    return out


def load_wikidata():
    raw = json.loads((SRC / "wikidata.json").read_text(encoding="utf-8"))
    by = {}
    for kind in ("icao", "iata"):
        for b in raw[kind]:
            key = (kind, b["code"]["value"])
            cur = by.setdefault(key, {"qid": b["item"]["value"].rsplit("/", 1)[1]})
            for f in ("ruLabel", "enLabel", "placeRu", "site"):
                if f in b and f not in cur:
                    cur[f] = b[f]["value"]
    return by


def clean_name_ru(name):
    name = name.replace("́", "")  # знаки ударения из Википедии
    name = re.sub(r"\s*\((аэропорт|аэродром)[^)]*\)\s*$", "", name, flags=re.I)
    name = re.sub(r"^(Международный\s+)?(аэропорт|аэродром)\s+", "", name, flags=re.I)
    name = re.sub(r"\s+(аэропорт|аэродром)$", "", name, flags=re.I)
    return name.strip(" «»\"")


def keyword_ru(keywords):
    for k in keywords.split(","):
        k = k.strip()
        if CYR.search(k) and not re.fullmatch(r"[А-ЯЁЬ]{3,4}", k):
            return clean_name_ru(k)
    return ""


def write_csv(path, rows):
    """CSV с булевыми значениями в виде true/false, как в JSON."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: str(v).lower() if isinstance(v, bool) else v for k, v in r.items()})


def load_terminals(info):
    """Терминалы: проверенные разделы flight-delayed.ru + добранные по сайтам аэропортов."""
    out = []
    for iata in sorted(info):
        for t in info[iata]["terminals"]:
            out.append({"iata": iata, "terminal": t["label"], "purpose": t["purpose"], "flights": t["flights"],
                        "source_url": f"{SITE}/airports/{iata.lower()}", "checked_at": info[iata]["checked_at"]})
    extra = SRC / "terminals_extra.csv"
    if extra.exists():
        for t in read_csv(extra):
            out.append({k: t[k] for k in ("iata", "terminal", "purpose", "flights", "source_url", "checked_at")})
    return sorted(out, key=lambda t: t["iata"])


def checkpoint(c):
    """Строка реестра Росгранстроя → поля таблицы border_checkpoints.csv."""
    cls = c["classification"]
    mode = ("постоянный" if "постоянный" in cls else "временный" if "временный" in cls
            else "нерегулярный" if "нерегулярн" in cls else "")
    kind = "грузо-пассажирский" if "грузо-пассажир" in cls else "пассажирский" if "пассажир" in cls else "грузовой"
    updated = re.search(r"обновлена (\d{4}-\d{2}-\d{2})", c["source_edition"])
    notes = re.sub(r"^(НЕ )?функционирует \(реестр Росгранстроя\);?\s*", "", c["notes"])
    return {
        "name": c["name_in_document"],
        "region_ru": c["location"],
        "iata": c["iata"],
        "icao": c["icao"],
        "kind": kind,
        "mode": mode,
        "classification": cls,
        "active": not c["notes"].startswith("НЕ "),
        "in_airports_csv": False,
        "record_updated": updated.group(1) if updated else "",
        "rosgranstroy_url": c["source_url"],
        "notes": notes,
    }


def site_pages(codes, refresh):
    path = SRC / "site_pages.json"
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if refresh:
        for code in codes:
            url = f"{SITE}/airports/{code.lower()}"
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "russian-airports-dataset/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    known[code] = r.status == 200
            except urllib.error.HTTPError as e:
                known[code] = False if e.code == 404 else known.get(code, False)
            time.sleep(1)
        path.write_text(json.dumps(dict(sorted(known.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    return known


def main():
    refresh = "--check-site" in sys.argv
    tf = TimezoneFinder()
    wd = load_wikidata()
    over = load_overrides()
    site = {r["iata"]: r for r in read_csv(SRC / "flight_delayed_airports.csv")}
    info = json.loads((SRC / "flight_delayed_terminals.json").read_text(encoding="utf-8"))
    terms = load_terminals(info)
    websites = {r["iata"]: r["official_website"] for r in read_csv(SRC / "websites.csv")}
    checkpoints = [checkpoint(c) for c in read_csv(SRC / "air_checkpoints.csv")]
    cp_by_code = {c[k]: c for c in checkpoints for k in ("iata", "icao") if c[k]}

    rows = []
    for a in read_csv(SRC / "ourairports_airports.csv"):
        ident = a["ident"]
        if a["iso_country"] != "RU" and ident not in CRIMEA:
            continue
        if a["type"] not in ("small_airport", "medium_airport", "large_airport"):
            continue
        iata = a["iata_code"]
        # Без IATA берём только аэродромы с ICAO-кодом из icao_code; gps_code у
        # мелких площадок — местные индексы, а не коды ИКАО.
        icao = a["icao_code"] or (a["gps_code"] if iata and re.fullmatch(r"[A-Z]{4}", a["gps_code"]) else "")
        if not (iata or icao) or icao in MILITARY or iata in MILITARY_IATA:
            continue
        w = wd.get(("icao", icao)) or wd.get(("iata", iata)) or {}
        s = site.get(iata, {})
        lat, lon = float(a["latitude_deg"]), float(a["longitude_deg"])
        region_ru, region_iso = CRIMEA.get(ident) or (REGION_RU.get(a["iso_region"], ""), a["iso_region"])
        cp = cp_by_code.get(iata) or cp_by_code.get(icao)
        if cp:
            cp["in_airports_csv"] = True
        row = {
            "iata": iata,
            "icao": icao,
            "name_ru": s.get("name_ru") or clean_name_ru(w.get("ruLabel", "")) or keyword_ru(a["keywords"]),
            "name_en": a["name"],
            "city_ru": s.get("city_ru") or re.sub(r"^городское поселение\s+", "", w.get("placeRu", "")),
            "city_en": a["municipality"],
            "region_ru": s.get("region_ru") or region_ru,
            "region_iso": region_iso,
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "elevation_m": round(int(a["elevation_ft"]) * 0.3048) if a["elevation_ft"] else "",
            "timezone": s.get("timezone") or tf.timezone_at(lat=lat, lng=lon) or "",
            "size": a["type"].replace("_airport", ""),
            "scheduled_service": a["scheduled_service"] == "yes",
            "international": bool(cp),
            "border_checkpoint": cp["name"] if cp else "",
            "border_checkpoint_active": cp["active"] if cp else "",
            "terminals": sum(t["iata"] == iata for t in terms) or "",
            "website": websites.get(iata) or info.get(iata, {}).get("website") or w.get("site", "") or a["home_link"],
            "wikidata": w.get("qid", ""),
            "ourairports_id": a["id"],
            "flight_delayed_url": "",
        }
        for field, value in over.get(iata or icao, {}).items():
            row[field] = {"true": True, "false": False}.get(value, value)
        rows.append(row)

    rows.sort(key=lambda r: (r["iata"] == "", r["iata"] or r["icao"]))
    pages = site_pages([r["iata"] for r in rows if r["iata"]], refresh)
    for r in rows:
        if r["iata"] and pages.get(r["iata"]):
            r["flight_delayed_url"] = f"{SITE}/airports/{r['iata'].lower()}"

    # Пункт пропуска с гражданским кодом, но без строки в справочнике — значит, фильтр что-то потерял.
    lost = [c["name"] for c in checkpoints if not c["in_airports_csv"] and c["iata"] and c["iata"] not in MILITARY_IATA]
    if lost:
        sys.exit(f"Пункты пропуска без аэропорта в справочнике: {lost}")

    known = {r["iata"] for r in rows}
    stray = sorted({t["iata"] for t in terms} - known)
    if stray:
        sys.exit(f"Терминалы у аэропортов вне справочника: {stray}")

    OUT.mkdir(exist_ok=True)
    write_csv(OUT / "airports.csv", rows)
    (OUT / "airports.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_csv(OUT / "terminals.csv", terms)
    write_csv(OUT / "border_checkpoints.csv", sorted(checkpoints, key=lambda c: c["name"]))

    intl = sum(r["international"] for r in rows)
    with_terms = len({t["iata"] for t in terms})
    print(f"аэропортов: {len(rows)}, международных: {intl}, с терминалами: {with_terms}, терминалов: {len(terms)}")


if __name__ == "__main__":
    main()
