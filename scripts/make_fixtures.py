"""Generate the sample data bundle used by the Data Studio (assets/samples/).

Run:  uv run python scripts/make_fixtures.py

Produces a self-contained bundle of 2 PDFs + 4 CSVs + 3 SQLite DBs so the demo
covers every input type the Studio understands:

  PDFs (knowledge-graph + Data-Doctor "read a document" demos)
    * project_falcon_brief.pdf       — one-page narrative (hand-authored)
    * acme_global_review_2025.pdf    — multi-page narrative with executives,
                                       subsidiaries, partner companies, projects,
                                       cities and dates. Designed so the text→KG
                                       extractor returns several distinct clusters.

  CSVs (Data Doctor + Explore demos)
    * customers_dirty.csv            — planted issues (nulls, dup row, mixed case,
                                       money-as-text, mixed date formats, outliers).
    * sales_2024.csv                 — clean transactional data for stats / charts.
    * weather_observations.csv       — time-series with numeric + categorical mix.
    * employees_messy.csv            — currency + locale + casing issues.

  SQLite DBs (single-DB and multi-DB ER graph demos)
    * shop.db                        — e-commerce, 4 linked tables with FKs +
                                       planted quality issues.
    * crm.db                         — customers + interactions.
    * billing.db                     — invoices + payments; customer_id overlaps
                                       crm.db so the multi-DB ER view draws a
                                       cross-database link.

Deterministic via a fixed RNG seed so test snapshots are stable.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "samples"
OUT.mkdir(parents=True, exist_ok=True)

RNG = random.Random(20260601)

COUNTRIES_RAW = ["VN", "vn", "Vietnam", "VIETNAM", "US", "USA", "us", "JP", "Japan", "JP "]
SEGMENTS = ["SMB", "Enterprise", "Consumer", "Government"]
CHANNELS = ["email", "phone", "chat", "in-person"]
PAY_METHODS = ["card", "bank_transfer", "wallet", "cash"]
CATEGORIES = ["Electronics", "Books", "Apparel", "Home", "Toys"]
INVOICE_STATUS = ["paid", "open", "overdue", "void"]


def _open(name: str) -> sqlite3.Connection:
    path = OUT / name
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _iso(d: date) -> str:
    return d.isoformat()


# --------------------------------------------------------------------------- #
# shop.db — single-database ER demo with planted quality issues
# --------------------------------------------------------------------------- #
def make_shop_db() -> None:
    conn = _open("shop.db")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            country TEXT,
            signup_date TEXT
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            price REAL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date TEXT,
            total REAL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            qty INTEGER,
            price REAL,
            FOREIGN KEY (order_id) REFERENCES orders(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
        """
    )

    # --- customers (50) — inject quality issues for Data Doctor to find -----
    customers = []
    base = date(2023, 1, 1)
    for i in range(1, 51):
        name = f"Customer {i:03d}"
        # 10% NULL emails, sporadic case noise on country, occasional whitespace.
        email = None if i % 10 == 0 else f"user{i:03d}@example.com"
        country = RNG.choice(COUNTRIES_RAW)
        signup = base + timedelta(days=RNG.randint(0, 700))
        customers.append((i, name, email, country, _iso(signup)))
    # Duplicate row (same fields except id) so duplicate detection has something to do.
    customers.append((51, customers[0][1], customers[0][2], customers[0][3], customers[0][4]))
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    # --- products (30) ------------------------------------------------------
    products = []
    for i in range(1, 31):
        products.append((i, f"Product {i:03d}", RNG.choice(CATEGORIES), round(RNG.uniform(5, 500), 2)))
    cur.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    # --- orders (200) — some negative totals (data error to flag) ------------
    orders = []
    for i in range(1, 201):
        cid = RNG.randint(1, 50)
        d = base + timedelta(days=RNG.randint(0, 900))
        total = round(RNG.uniform(20, 2000), 2)
        if i % 37 == 0:  # planted bad data
            total = -abs(total)
        orders.append((i, cid, _iso(d), total))
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)

    # --- order_items (~600) -------------------------------------------------
    items = []
    next_id = 1
    for order_id in range(1, 201):
        for _ in range(RNG.randint(1, 5)):
            pid = RNG.randint(1, 30)
            qty = RNG.randint(1, 6)
            price = round(RNG.uniform(5, 500), 2)
            items.append((next_id, order_id, pid, qty, price))
            next_id += 1
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)

    conn.commit()
    conn.close()
    print(f"  wrote {OUT / 'shop.db'}  ({len(customers)} customers, {len(orders)} orders, {len(items)} items)")


# --------------------------------------------------------------------------- #
# crm.db + billing.db — multi-database ER demo
# --------------------------------------------------------------------------- #
def make_crm_db() -> int:
    """Returns the number of customers (so billing.db can reuse the same id space)."""
    conn = _open("crm.db")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            segment TEXT,
            country TEXT,
            signup_date TEXT
        );
        CREATE TABLE interactions (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            channel TEXT,
            interaction_date TEXT,
            notes TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
        """
    )
    n_customers = 80
    base = date(2023, 6, 1)
    customers = [
        (i, f"Account {i:03d}", RNG.choice(SEGMENTS),
         RNG.choice(["VN", "US", "JP", "SG", "DE"]),
         _iso(base + timedelta(days=RNG.randint(0, 500))))
        for i in range(1, n_customers + 1)
    ]
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    interactions = []
    for i in range(1, 301):
        cid = RNG.randint(1, n_customers)
        interactions.append((
            i, cid, RNG.choice(CHANNELS),
            _iso(base + timedelta(days=RNG.randint(0, 500))),
            RNG.choice(["follow-up", "demo", "support", "renewal", "complaint"]),
        ))
    cur.executemany("INSERT INTO interactions VALUES (?,?,?,?,?)", interactions)

    conn.commit()
    conn.close()
    print(f"  wrote {OUT / 'crm.db'}  ({n_customers} customers, {len(interactions)} interactions)")
    return n_customers


def make_billing_db(crm_customer_count: int) -> None:
    conn = _open("billing.db")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            issue_date TEXT,
            amount REAL,
            status TEXT
        );
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER NOT NULL,
            paid_date TEXT,
            amount REAL,
            method TEXT,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id)
        );
        """
    )
    base = date(2024, 1, 1)
    invoices = []
    for i in range(1, 151):
        cid = RNG.randint(1, crm_customer_count)  # overlap with crm.db
        invoices.append((
            i, cid, _iso(base + timedelta(days=RNG.randint(0, 300))),
            round(RNG.uniform(50, 5000), 2),
            RNG.choices(INVOICE_STATUS, weights=[6, 3, 2, 1])[0],
        ))
    cur.executemany("INSERT INTO invoices VALUES (?,?,?,?,?)", invoices)

    payments = []
    next_id = 1
    for inv_id, _cid, issue_d, amount, status in invoices:
        if status in {"paid", "overdue"} and RNG.random() < 0.85:
            paid = date.fromisoformat(issue_d) + timedelta(days=RNG.randint(1, 60))
            payments.append((
                next_id, inv_id, _iso(paid),
                round(amount * RNG.uniform(0.5, 1.0), 2),
                RNG.choice(PAY_METHODS),
            ))
            next_id += 1
    cur.executemany("INSERT INTO payments VALUES (?,?,?,?,?)", payments)

    conn.commit()
    conn.close()
    print(f"  wrote {OUT / 'billing.db'}  ({len(invoices)} invoices, {len(payments)} payments)")


# --------------------------------------------------------------------------- #
# CSV fixtures
# --------------------------------------------------------------------------- #
def _write_csv(name: str, header: list[str], rows: list[list[str]]) -> None:
    path = OUT / name
    # Use a manual writer rather than csv.writer so the "mess" we plant (leading
    # spaces, currency symbols, mixed quoting) survives intact — csv.writer would
    # normalise some of these and weaken the Data-Doctor demo.
    lines = [",".join(header)]
    for r in rows:
        out = []
        for cell in r:
            if "," in cell or '"' in cell:
                out.append('"' + cell.replace('"', '""') + '"')
            else:
                out.append(cell)
        lines.append(",".join(out))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  wrote {path}  ({len(rows)} rows)")


def make_csvs() -> None:
    """Re-emit the four CSV demo files. Content matches what was hand-tuned for
    the §12.1 Data Doctor walkthroughs (planted nulls, dupes, currency text,
    mixed date formats, locale-mixed casing)."""

    # --- customers_dirty.csv — the headline Data Doctor demo ---------------
    _write_csv(
        "customers_dirty.csv",
        ["id", "full_name", "age", "country", "signup_date", "spend", "status", "notes"],
        [
            ["1", "Alice Smith", "34", "USA", "2023-01-05", "$1,200", "active", "VIP"],
            ["2", " Bob Jones ", "", "usa", "02/14/2023", "$95", "active", ""],
            ["3", "Carla Diaz", "29", "Canada", "2023/03/01", "$2,400", "active", "  needs review  "],
            ["4", "David Lee", "41", "canada", "04/22/2023", "$0", "active", ""],
            ["5", "Eve Adams", "999", "USA", "2023-05-10", "$1,050", "active", "outlier age"],
            ["6", "Frank Wu", "38", "Usa", "06/30/2023", "$780", "active", ""],
            ["7", "Grace Kim", "", "Canada", "2023-07-15", "$3,300", "active", ""],
            ["8", "Henry Ford", "52", "USA", "08/01/2023", "$540", "active", "note"],
            ["9", "Ivy Chen", "27", "canada", "2023-09-09", "$1,800", "active", ""],
            ["10", "Jack Ma", "45", "USA", "10/10/2023", "$6,000", "active", ""],
            # Duplicate row — exact copy of id=10 so duplicate detection fires.
            ["10", "Jack Ma", "45", "USA", "10/10/2023", "$6,000", "active", ""],
            ["11", "Karen Poe", "", "usa", "2023-11-11", "$210", "active", ""],
            ["12", "Liam O'Neil", "39", "Ireland", "12/12/2023", "$1,400", "inactive", "churn risk"],
            ["13", "Mia Garcia", "31", "Mexico", "2024-01-05", "$880", "active", ""],
            ["14", "Noah Kim", "28", "korea", "01/22/2024", "$2,150", "active", ""],
            ["15", "  Olivia Park ", "", "USA", "2024-02-14", "$320", "active", "   "],
            ["16", "Peter Vu", "47", "vn", "2024-03-03", "$4,900", "active", "VIP"],
            ["17", "Quinn Tran", "33", "Vietnam", "03/30/2024", "$1,250", "inactive", ""],
            ["18", "Ravi Singh", "", "India", "2024-04-10", "$680", "active", ""],
            ["19", "Sofia Rossi", "29", "Italy", "2024-05-21", "$2,800", "active", ""],
            ["20", "Tom Becker", "888", "Germany", "2024-06-01", "$1,100", "active", "outlier age"],
        ],
    )

    # --- sales_2024.csv — clean, for stats / charts ------------------------
    sales_rows: list[list[str]] = []
    regions = ["North", "South", "East", "West"]
    products = [("Keyboard", 45.00), ("Mouse", 18.50), ("Monitor", 229.00),
                ("Headset", 59.99), ("Webcam", 79.00)]
    base = date(2024, 1, 1)
    for i in range(1, 41):
        d = base + timedelta(days=RNG.randint(0, 360))
        region = RNG.choice(regions)
        prod, unit = RNG.choice(products)
        qty = RNG.randint(1, 10)
        sales_rows.append([
            str(1000 + i), d.isoformat(), region, prod, str(qty),
            f"{unit:.2f}", f"{unit * qty:.2f}",
        ])
    sales_rows.sort(key=lambda r: r[1])
    _write_csv(
        "sales_2024.csv",
        ["order_id", "order_date", "region", "product", "quantity", "unit_price", "total"],
        sales_rows,
    )

    # --- weather_observations.csv — time series with numeric + categorical -
    weather_rows: list[list[str]] = []
    stations = [("WX001", "Hanoi"), ("WX002", "Seoul"), ("WX003", "Tokyo"),
                ("WX004", "Singapore"), ("WX005", "Bangkok")]
    for day in range(7):
        for sid, city in stations:
            for hour in (6, 12, 18):
                t = date(2024, 6, 1) + timedelta(days=day)
                temp = round(22 + RNG.uniform(-2, 14), 1)
                hum = RNG.randint(45, 92)
                wind = round(RNG.uniform(2, 18), 1)
                precip = round(RNG.choice([0.0, 0.0, 0.0, RNG.uniform(0.2, 6.5)]), 1)
                weather_rows.append([
                    sid, f"{t.isoformat()} {hour:02d}:00", city,
                    f"{temp}", str(hum), f"{wind}", f"{precip}",
                ])
    _write_csv(
        "weather_observations.csv",
        ["station_id", "observed_at", "city", "temperature_c", "humidity_pct",
         "wind_kph", "precipitation_mm"],
        weather_rows,
    )

    # --- employees_messy.csv — currency + locale + casing chaos ------------
    _write_csv(
        "employees_messy.csv",
        ["emp_id", "full_name", "department", "country", "hire_date", "salary", "years_at_company"],
        [
            ["E001", " Alice Johnson ", "Engineering", "usa", "2019-04-15", "$92,000", "5"],
            ["E002", "bob smith", "marketing", "USA", "15/03/2020", "$65,500", "4"],
            ["E003", "Carol DAVIES", "Engineering", "UK", "2018-11-02", "£81,000", "6"],
            ["E004", "DAVID lee", "Sales", "uk", "2021-08-19", "$58,000", ""],
            ["E005", "  Emma  Patel  ", "engineering", "India", "2022-01-10", "₹2,400,000", "3"],
            ["E006", "frank obrien", "SALES", "IRL", "2017-06-25", "€72,500", "8"],
            ["E007", "Grace Kim", "marketing", "KOR", "2020/09/14", "$71,000", "5"],
            ["E008", "henry zhang", "Engineering", "china", "2019-12-01", "¥800,000", "6"],
            ["E009", "Isabella ROSSI", "sales", "Italy", "03-02-2021", "€61,000", "4"],
            ["E010", "Jack OConnor", "Engineering", "Ireland", "2018-07-30", "€85,000", "7"],
            ["E011", "kira sato", "Engineering", "Japan", "2020-05-20", "¥9,200,000", "5"],
            ["E012", "Liam Brown", "Sales", "Canada", "2019-09-09", "CAD 74,000", "6"],
            ["E013", "  Maya  Singh", "marketing", "in", "2021-12-12", "₹2,000,000", "3"],
            ["E014", "Noah Tran", "engineering", "VN", "2022-06-01", "₫1,200,000,000", "2"],
            ["E015", "Olga Petrov", "Sales", "Russia", "01/01/2020", "₽3,500,000", "5"],
        ],
    )


# --------------------------------------------------------------------------- #
# PDF fixtures (knowledge-graph + read-document demos)
# --------------------------------------------------------------------------- #
def _write_text_pdf(filename: str, pages: list[str]) -> None:
    """Write a minimal multi-page PDF with Helvetica text using pypdf primitives.

    Each input page is a string; lines are wrapped at ~85 chars and laid out
    top-down with one BT…ET block per line so pypdf's text extractor recovers
    them in order. We embed the Type1 base font (Helvetica) so no font file is
    needed — this matches the loader path used in tests/test_text_kg.py.
    Non-Latin-1 characters (em-dash, curly quotes, …) are replaced with ASCII
    equivalents before encoding, otherwise they'd be dropped to "?" by latin-1.
    """
    from pypdf import PageObject, PdfWriter
    from pypdf.generic import ContentStream, DictionaryObject, NameObject

    # Substitutions that keep the document readable when the font is Latin-1
    # base Helvetica. Extend if a new page introduces another non-ASCII glyph.
    _ASCII_FALLBACK = str.maketrans({
        "\u2014": "-", "\u2013": "-",  # em-dash, en-dash
        "\u2018": "'", "\u2019": "'",  # curly single quotes
        "\u201C": '"', "\u201D": '"',  # curly double quotes
        "\u2026": "...",                # ellipsis
        "\u00A0": " ",                  # non-breaking space
    })

    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})

    for body in pages:
        body = body.translate(_ASCII_FALLBACK)
        page = PageObject.create_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = resources

        lines: list[str] = []
        for paragraph in body.split("\n"):
            paragraph = paragraph.rstrip()
            if not paragraph:
                lines.append("")
                continue
            # naive word-wrap so long paragraphs lay out on multiple lines
            words = paragraph.split(" ")
            cur = ""
            for w in words:
                if len(cur) + len(w) + 1 > 85 and cur:
                    lines.append(cur)
                    cur = w
                else:
                    cur = (cur + " " + w) if cur else w
            if cur:
                lines.append(cur)

        # PDF coordinate origin is bottom-left. Start at y=752 and step down 16pt
        # per line, leaving generous margins on all sides.
        stream_parts = ["BT", "/F1 11 Tf", "50 752 Td"]
        first = True
        for ln in lines:
            safe = ln.replace("\\", "\\\\").replace("(", "[").replace(")", "]")
            if not first:
                stream_parts.append("0 -16 Td")
            stream_parts.append(f"({safe}) Tj")
            first = False
        stream_parts.append("ET")
        stream_bytes = ("\n".join(stream_parts) + "\n").encode("latin-1", errors="replace")

        content = ContentStream(None, writer)
        content.set_data(stream_bytes)
        page[NameObject("/Contents")] = writer._add_object(content)
        writer.add_page(page)

    path = OUT / filename
    with path.open("wb") as fh:
        writer.write(fh)
    print(f"  wrote {path}  ({len(pages)} page{'s' if len(pages) != 1 else ''})")


def make_pdfs() -> None:
    """Two PDFs covering the spread of "what an LLM should be able to read out of
    a document": a tight one-page brief, and a multi-page narrative with many
    entities and relations so the text-knowledge-graph clustering demo has
    visible communities to draw."""

    # --- project_falcon_brief.pdf — single-page, simple narrative ----------
    falcon = (
        "Project Falcon — Internal Brief\n"
        "Prepared by the Acme Corp Strategy Office, 14 March 2024.\n"
        "\n"
        "Project Falcon is the next-generation logistics platform that Acme Corp will launch "
        "across Southeast Asia in Q3 2024. Jack Ma, who heads the Strategy Office, is the "
        "executive sponsor. Globex Ltd has been selected as the regional implementation "
        "partner; Globex Ltd is headquartered in Berlin and operates a delivery hub in "
        "Singapore. Initech Inc will supply the routing software under a three-year contract.\n"
        "\n"
        "The Falcon steering committee meets monthly in Hanoi and includes representatives "
        "from Acme Robotics and Acme Cloud. Alice Smith leads the Hanoi engineering team, "
        "while Bob Jones manages the Singapore launch site. Carol Davies, who reports to "
        "Jack Ma, owns the partner relationship with Globex Ltd.\n"
        "\n"
        "Initial milestones: pilot in Singapore (June 2024), regional roll-out (September "
        "2024), and full handover to Acme Robotics operations (December 2024)."
    )
    _write_text_pdf("project_falcon_brief.pdf", [falcon])

    # --- acme_global_review_2025.pdf — multi-page, KG-friendly narrative ---
    # Each page focuses on a distinct sub-domain (subsidiary, partner, region)
    # so the text→KG extractor + new clustering yields several legible
    # communities instead of one giant hairball.
    page1 = (
        "Acme Corp — 2025 Global Operations Review (Page 1 of 4)\n"
        "Executive summary by Jack Ma, Chief Executive, 11 January 2025.\n"
        "\n"
        "In 2024 Acme Corp continued its transformation from a single-product manufacturer "
        "into a federation of four operating subsidiaries: Acme Robotics, Acme Cloud, Acme "
        "Health, and Acme Foundry. The federation reports to the Acme Corp board, chaired by "
        "Diana Cole. Jack Ma serves as Chief Executive, supported by Chief Operating Officer "
        "Eric Tan and Chief Financial Officer Fiona Park.\n"
        "\n"
        "Acme Robotics, led by Alice Smith, accounts for 41 percent of revenue. The "
        "subsidiary is headquartered in Singapore and operates manufacturing lines in Hanoi "
        "and Bangkok. Acme Cloud, led by Bob Jones, is headquartered in Berlin and provides "
        "the platform that powers Project Falcon, Project Atlas, and Project Nebula.\n"
        "\n"
        "Acme Health, founded in 2023 and led by Carol Davies, operates from Sao Paulo and "
        "partners with Umbrella Industries on the Aurora clinical trial. Acme Foundry, led "
        "by David Lee, runs the legacy metallurgy plants in Pittsburgh and Birmingham."
    )
    page2 = (
        "Acme Corp — 2025 Global Operations Review (Page 2 of 4)\n"
        "Section: Strategic Partnerships.\n"
        "\n"
        "Globex Ltd remained Acme Corp's primary logistics partner throughout 2024. Globex "
        "Ltd is headquartered in Berlin and is led by Henrik Mueller; the company operates "
        "delivery hubs in Singapore, Bangkok, and Sao Paulo. Under the renewed three-year "
        "agreement, Globex Ltd handles fulfilment for Project Falcon and for Acme Robotics "
        "spare-parts distribution.\n"
        "\n"
        "Initech Inc supplied routing software for Project Falcon and Project Atlas. Initech "
        "Inc is based in Boston and was acquired by Acme Cloud in October 2024; its founder "
        "Greg Lin now reports to Bob Jones. The acquisition gave Acme Cloud control of the "
        "Atlas optimisation engine.\n"
        "\n"
        "Umbrella Industries co-funds the Aurora trial alongside Acme Health. Umbrella "
        "Industries is headquartered in Zurich and is chaired by Helena Roth. The Aurora "
        "trial began enrolment in Sao Paulo in March 2024 and expanded to Mexico City in "
        "September. Carol Davies and Helena Roth co-chair the trial steering committee."
    )
    page3 = (
        "Acme Corp — 2025 Global Operations Review (Page 3 of 4)\n"
        "Section: Project Portfolio.\n"
        "\n"
        "Project Falcon launched its Singapore pilot in June 2024 and went regional in "
        "September. The project is sponsored by Jack Ma and managed by Alice Smith; Globex "
        "Ltd is the logistics partner and Initech Inc supplies the routing engine.\n"
        "\n"
        "Project Atlas is the second-generation analytics platform built by Acme Cloud. It "
        "is led by Bob Jones and uses the Atlas optimisation engine acquired with Initech "
        "Inc. Atlas powers reporting for Acme Robotics in Hanoi and for Acme Health in Sao "
        "Paulo.\n"
        "\n"
        "Project Nebula is an Acme Cloud research initiative on edge AI. Nebula is led by "
        "Mia Patel, who reports to Bob Jones, and runs experiments at the Berlin and Tokyo "
        "labs. The Nebula team published two papers in 2024 with researchers from Globex "
        "Ltd's R&D unit in Berlin.\n"
        "\n"
        "Project Aurora, the joint Acme Health and Umbrella Industries clinical trial, "
        "enrolled 1,200 patients in Sao Paulo and 600 in Mexico City. Aurora is co-chaired "
        "by Carol Davies and Helena Roth."
    )
    page4 = (
        "Acme Corp — 2025 Global Operations Review (Page 4 of 4)\n"
        "Section: People and Governance.\n"
        "\n"
        "The Acme Corp board added two independent directors in 2024: Diana Cole (chair), "
        "previously of Stark Industries, and Omar Faruq, previously of Wayne Enterprises. "
        "The board now comprises seven members.\n"
        "\n"
        "Executive committee: Jack Ma (CEO), Eric Tan (COO), Fiona Park (CFO), Alice Smith "
        "(CEO, Acme Robotics), Bob Jones (CEO, Acme Cloud), Carol Davies (CEO, Acme Health), "
        "and David Lee (CEO, Acme Foundry). Alice Smith also chairs the safety committee.\n"
        "\n"
        "Regional general managers: Hanoi office is led by Linh Nguyen who reports to Alice "
        "Smith; the Berlin office is led by Klaus Becker who reports to Bob Jones; the Sao "
        "Paulo office is led by Lucia Mendes who reports to Carol Davies; the Pittsburgh "
        "office is led by Robert King who reports to David Lee.\n"
        "\n"
        "Looking ahead to 2025, Jack Ma confirmed that Acme Corp will open a new Acme Cloud "
        "data centre in Tokyo and that Acme Health will begin a partnership with Wayne "
        "Enterprises in Gotham on a follow-on trial code-named Project Beacon."
    )
    _write_text_pdf("acme_global_review_2025.pdf", [page1, page2, page3, page4])


def main() -> None:
    print(f"Generating sample bundle in {OUT} ...")
    make_shop_db()
    n = make_crm_db()
    make_billing_db(n)
    make_csvs()
    make_pdfs()
    print("Done.")


if __name__ == "__main__":
    main()
