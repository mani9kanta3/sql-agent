"""
Fill the database with data that looks like a real shop's.

Two things this script cares about that a normal seed script does not.

**The shape of the data is part of the test.** Most bills have no
customer, because most people walk in and pay cash. A few bills are
cancelled. Some payments are partial. The archive covers 2023 and 2024
and the live table starts in 2025. Every one of those is something a
query can get wrong in a way that still runs, which is the only kind of
mistake worth building an agent to catch.

**It is reproducible.** random.seed() is fixed, so the same numbers come
out every time. The evaluation compares the agent's answer to a known
correct one, and ground truth that changed every time the database was
rebuilt would be worthless.

This runs as the admin user, not the agent's read only role, for the
obvious reason. It is in scripts/ and not in app/ so that there is no
code inside app/ capable of writing to the database at all.

Run:  python -m scripts.seed
"""

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg2

from app import config

random.seed(20260830)

TODAY = date(2026, 8, 30)

# The current system went live at the start of 2025. Everything before
# that is in bill_archive with different column names, which is the
# whole point of that table existing.
LIVE_FROM = date(2025, 1, 1)
ARCHIVE_FROM = date(2023, 1, 1)
ARCHIVE_TO = date(2024, 12, 31)


def admin_connection():
    """The owner connection. Only this file and setup_database.py use it."""
    return psycopg2.connect(
        dbname=config.DB_NAME,
        user=config.DB_ADMIN_USER,
        password=config.DB_ADMIN_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        sslmode=config.DB_SSLMODE,
    )


# ------------------------------------------------------------ the data

CATEGORIES = ["Plumbing", "Electrical", "Fasteners", "Paint", "Tools", "Cement and Sand"]

SUPPLIERS = [
    # name, phone, gst_no, is_active
    ("Sri Venkateswara Traders", "9848012301", "37AABCS1429B1ZP", True),
    ("Anjaneya Hardware Supply", "9866045512", "36AACCA9812K1Z4", True),
    ("Balaji Steel and Pipes", "9701223344", "37AAGCB4471M1ZQ", True),
    ("Krishna Paints Depot", "9885567712", "36AADCK2290J1ZR", True),
    ("Local Sand Supplier", "9491100234", None, True),          # not registered
    ("Ramesh Nuts and Bolts", "9440778812", None, True),        # not registered
    ("Nandi Electricals", "9963321109", "37AAECN5518L1ZT", True),
    ("Old Town Timber", "9848990011", "37AABFO2210C1ZG", False),  # no longer used
]

EMPLOYEES = [
    # name, role, join_dt, mgr_index (None for the owner), salary
    ("Manikanta Pudi", "OWNER", date(2022, 4, 1), None, None),
    ("Ravi Teja", "CASHIER", date(2023, 6, 15), 0, 22000),
    ("Sunitha Rao", "CASHIER", date(2024, 2, 1), 0, 21000),
    ("Naveen Kumar", "CASHIER", date(2025, 7, 20), 0, 19000),
    ("Prasad Reddy", "STOREKEEPER", date(2023, 1, 10), 0, 24000),
    ("Lakshmi Devi", "STOREKEEPER", date(2025, 11, 5), 0, 20000),
]

CUSTOMER_NAMES = [
    "Sai Constructions", "Vijay Builders", "Sri Rama Contractors", "MRK Interiors",
    "Anand Plumbing Works", "Guru Electricals", "Bharath Civil Works", "Kiran Enterprises",
    "Sravani Traders", "Modern Home Builders", "Chandra Painting Services",
    "Vamsi Krishna", "Ramesh Babu", "Sudhakar Rao", "Padma Latha",
    "Nagarjuna Constructions", "SVR Developers", "Teja Interiors",
    "Mahesh Hardware Works", "Prakash Rao", "Lalitha Kumari", "Yeswanth Reddy",
    "Girish Enterprises", "Deepak Sharma", "Kavitha Reddy",
]

# name, sku, unit, sell_price, category index, reorder level
PRODUCTS = [
    ("PVC Pipe 1 inch", "PLM-PVC-1", "metre", 82.00, 0, 50),
    ("PVC Pipe 2 inch", "PLM-PVC-2", "metre", 148.00, 0, 40),
    ("CPVC Elbow 3/4", "PLM-ELB-34", "piece", 34.00, 0, 100),
    ("Brass Tap Long Body", "PLM-TAP-LB", "piece", 640.00, 0, 15),
    ("Teflon Tape", "PLM-TFL-01", "piece", 12.00, 0, 200),
    ("Bathroom Shower Set", "PLM-SHW-01", "piece", 1850.00, 0, 8),
    ("Water Tank Connector", "PLM-CON-01", "piece", 96.00, 0, 30),

    ("Copper Wire 1.5sqmm", "ELC-WIR-15", "metre", 28.00, 1, 300),
    ("Copper Wire 2.5sqmm", "ELC-WIR-25", "metre", 44.00, 1, 300),
    ("Modular Switch 6A", "ELC-SWT-6A", "piece", 78.00, 1, 100),
    ("MCB 16A Single Pole", "ELC-MCB-16", "piece", 285.00, 1, 25),
    ("LED Bulb 9W", "ELC-LED-9W", "piece", 118.00, 1, 60),
    ("Ceiling Fan 1200mm", "ELC-FAN-12", "piece", 2450.00, 1, 6),
    ("PVC Conduit Pipe", "ELC-CDT-20", "metre", 26.00, 1, 200),
    ("Extension Board 4 Way", "ELC-EXT-04", "piece", 420.00, 1, 20),

    ("Wood Screw 2 inch", "FST-SCR-2I", "box", 145.00, 2, 30),
    ("Wood Screw 3 inch", "FST-SCR-3I", "box", 190.00, 2, 30),
    ("Iron Nail 3 inch", "FST-NAL-3I", "kg", 88.00, 2, 40),
    ("Anchor Bolt 10mm", "FST-BLT-10", "piece", 46.00, 2, 80),
    ("Hex Nut 12mm", "FST-NUT-12", "kg", 132.00, 2, 25),
    ("Door Hinge 4 inch", "FST-HNG-04", "piece", 52.00, 2, 60),
    ("Washer Set Assorted", "FST-WSH-AS", "box", 98.00, 2, 20),

    ("Emulsion Paint White 20L", "PNT-EMU-W20", "litre", 3850.00, 3, 5),
    ("Emulsion Paint Ivory 10L", "PNT-EMU-I10", "litre", 2100.00, 3, 6),
    ("Enamel Paint Black 1L", "PNT-ENM-B1", "litre", 340.00, 3, 20),
    ("Primer 4L", "PNT-PRM-04", "litre", 720.00, 3, 12),
    ("Paint Brush 3 inch", "PNT-BRS-03", "piece", 85.00, 3, 40),
    ("Roller with Tray", "PNT-ROL-01", "piece", 210.00, 3, 15),
    ("Wall Putty 20kg", "PNT-PTY-20", "kg", 640.00, 3, 15),

    ("Claw Hammer 500g", "TLS-HMR-05", "piece", 385.00, 4, 12),
    ("Screwdriver Set 6pc", "TLS-SDR-06", "box", 295.00, 4, 15),
    ("Measuring Tape 5m", "TLS-TAP-05", "piece", 145.00, 4, 25),
    ("Adjustable Spanner 10", "TLS-SPN-10", "piece", 320.00, 4, 12),
    ("Hand Saw 18 inch", "TLS-SAW-18", "piece", 410.00, 4, 8),
    ("Drill Machine 13mm", "TLS-DRL-13", "piece", 3250.00, 4, 4),
    ("Plier Insulated 8 inch", "TLS-PLR-08", "piece", 265.00, 4, 15),
    ("Spirit Level 600mm", "TLS-LVL-60", "piece", 480.00, 4, 8),

    ("OPC Cement 50kg", "CEM-OPC-50", "box", 415.00, 5, 40),
    ("PPC Cement 50kg", "CEM-PPC-50", "box", 385.00, 5, 40),
    ("River Sand", "CEM-SND-01", "kg", 2.20, 5, 2000),
    ("Blue Metal 20mm", "CEM-MTL-20", "kg", 1.80, 5, 2000),
]

SETTINGS = [
    ("shop_name", "Anvil Hardware"),
    ("receipt_footer", "Goods once sold are not taken back."),
    ("gst_number", "37AAHCA9911F1ZV"),
    ("currency", "INR"),
    ("low_stock_alert", "on"),
    ("till_version", "2.4.1"),
]


def random_datetime(start, end):
    """A timestamp somewhere between two dates, during shop hours."""
    span = (end - start).days
    day = start + timedelta(days=random.randint(0, max(span, 0)))
    # The shop opens at nine and shuts at eight.
    hour = random.randint(9, 19)
    return datetime(day.year, day.month, day.day, hour, random.randint(0, 59))


def money(value):
    """Two decimal places, always. Money never gets to be a float here."""
    return Decimal(str(round(value, 2)))


def seed():
    connection = admin_connection()
    cursor = connection.cursor()

    print("categories, suppliers, employees, customers")

    cursor.executemany(
        "INSERT INTO categories (cat_name) VALUES (%s)",
        [(name,) for name in CATEGORIES],
    )

    cursor.executemany(
        "INSERT INTO suppliers (supp_name, phone_no, addr, gst_no, is_active) "
        "VALUES (%s, %s, %s, %s, %s)",
        [
            (name, phone, f"Shop {index + 1}, Main Bazaar Road, Vijayawada", gst, active)
            for index, (name, phone, gst, active) in enumerate(SUPPLIERS)
        ],
    )

    # Employees are inserted one at a time because mgr_id points at a
    # row in this same table, so the owner has to exist before anyone
    # can report to them.
    employee_ids = []
    for name, role, join_dt, manager_index, salary in EMPLOYEES:
        manager_id = employee_ids[manager_index] if manager_index is not None else None
        cursor.execute(
            "INSERT INTO employees (emp_name, role, join_dt, mgr_id, salary) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING emp_id",
            (name, role, join_dt, manager_id, salary),
        )
        employee_ids.append(cursor.fetchone()[0])

    customer_ids = []
    for index, name in enumerate(CUSTOMER_NAMES):
        cursor.execute(
            "INSERT INTO customers (cust_name, phone_no, addr, is_credit_customer, created_dt) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING cust_id",
            (
                name,
                f"98{random.randint(10000000, 99999999)}",
                f"Plot {index + 11}, Sector {random.randint(1, 9)}, Vijayawada",
                index % 3 == 0,  # roughly a third buy on credit
                LIVE_FROM - timedelta(days=random.randint(0, 700)),
            ),
        )
        customer_ids.append(cursor.fetchone()[0])

    # -------------------------------------------------------- products

    print("products")

    cursor.execute("SELECT cat_id FROM categories ORDER BY cat_id")
    category_ids = [row[0] for row in cursor.fetchall()]

    product_ids = []
    product_prices = {}
    for name, sku, unit, price, category_index, reorder in PRODUCTS:
        # Stock is set so that a handful of products are genuinely below
        # their reorder level, because "what is running low" is one of
        # the questions and it should have a real answer.
        stock = random.randint(0, reorder) if random.random() < 0.18 else random.randint(reorder, reorder * 6)
        cursor.execute(
            "INSERT INTO products (prod_name, sku, unit, sell_price, stock_qty, reorder_lvl, cat_id, created_dt) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING prod_id",
            (name, sku, unit, money(price), stock, reorder,
             category_ids[category_index], date(2024, 12, 1)),
        )
        product_id = cursor.fetchone()[0]
        product_ids.append(product_id)
        product_prices[product_id] = money(price)

    # The dead table. A stale subset with old prices, so that anyone who
    # joins it instead of products gets numbers that are wrong but not
    # obviously wrong.
    cursor.executemany(
        "INSERT INTO tbl_prod_master_old (p_id, p_name, p_code, rate, active_flag) "
        "VALUES (%s, %s, %s, %s, %s)",
        [
            (index + 1, name, sku.replace("-", ""), money(price * 0.85), "Y" if index % 4 else "N")
            for index, (name, sku, _unit, price, _cat, _reorder) in enumerate(PRODUCTS[:15])
        ],
    )

    # -------------------------------------------------- goods received

    print("stock entries and purchase orders")

    cursor.execute("SELECT supp_id FROM suppliers WHERE is_active ORDER BY supp_id")
    supplier_ids = [row[0] for row in cursor.fetchall()]
    storekeeper_ids = [employee_ids[4], employee_ids[5]]

    entries = []
    for _ in range(320):
        product_id = random.choice(product_ids)
        entries.append((
            product_id,
            random.choice(supplier_ids),
            random.randint(10, 200),
            money(float(product_prices[product_id]) * random.uniform(0.55, 0.78)),
            random_datetime(LIVE_FROM, TODAY),
            random.choice(storekeeper_ids),
        ))
    cursor.executemany(
        "INSERT INTO stock_entries (prod_id, supp_id, qty, cost_price, recv_dt, recv_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        entries,
    )

    for _ in range(45):
        supplier_id = random.choice(supplier_ids)
        po_dt = LIVE_FROM + timedelta(days=random.randint(0, (TODAY - LIVE_FROM).days))
        status = random.choices(
            ["CLOSED", "PARTIAL", "OPEN", "CANCELLED"],
            weights=[60, 15, 20, 5],
        )[0]
        # A fifth of orders never got a confirmed delivery date.
        expected = None if random.random() < 0.2 else po_dt + timedelta(days=random.randint(3, 21))

        cursor.execute(
            "INSERT INTO purchase_orders (supp_id, po_dt, expected_dt, status, total_amt) "
            "VALUES (%s, %s, %s, %s, 0) RETURNING po_id",
            (supplier_id, po_dt, expected, status),
        )
        po_id = cursor.fetchone()[0]

        po_total = Decimal("0")
        for product_id in random.sample(product_ids, random.randint(1, 5)):
            ordered = random.randint(10, 120)
            if status == "CLOSED":
                received = ordered
            elif status == "PARTIAL":
                received = random.randint(1, ordered - 1)
            else:
                received = 0

            unit_cost = money(float(product_prices[product_id]) * random.uniform(0.55, 0.78))
            po_total += unit_cost * ordered

            cursor.execute(
                "INSERT INTO po_lines (po_id, prod_id, qty_ordered, qty_received, unit_cost) "
                "VALUES (%s, %s, %s, %s, %s)",
                (po_id, product_id, ordered, received, unit_cost),
            )

        cursor.execute(
            "UPDATE purchase_orders SET total_amt = %s WHERE po_id = %s",
            (money(float(po_total)), po_id),
        )

    # ----------------------------------------------------------- bills

    print("bills and bill items")

    cashier_ids = [employee_ids[1], employee_ids[2], employee_ids[3]]
    bill_number = 0
    payments = []

    # Five customers stopped coming during 2025. Real shops have them,
    # and without them "which customers have not bought in six months"
    # correctly returns nothing, which makes it a much weaker question
    # than it looks. Splitting the list here gives that question a real
    # answer to find.
    dormant_ids = set(customer_ids[-5:])
    DORMANT_LAST_BILL = date(2025, 9, 30)

    for _ in range(1400):
        bill_number += 1
        bill_dt = random_datetime(LIVE_FROM, TODAY)

        # The important line in this file. Most sales are walk in, so
        # most bills have no customer. A per customer revenue query that
        # uses an inner join loses about seventy percent of the shop's
        # takings and looks completely fine while doing it.
        customer_id = random.choice(customer_ids) if random.random() < 0.30 else None

        # A customer who stopped coming cannot appear on a recent bill.
        if customer_id in dormant_ids and bill_dt.date() > DORMANT_LAST_BILL:
            customer_id = None

        status = random.choices(["PAID", "PARTIAL", "CANCELLED"], weights=[88, 8, 4])[0]

        lines = []
        subtotal = Decimal("0")
        for product_id in random.sample(product_ids, random.randint(1, 6)):
            qty = random.randint(1, 12)
            price = product_prices[product_id]
            line_total = price * qty
            subtotal += line_total
            lines.append((product_id, qty, price, line_total))

        # A discount on roughly one bill in five, and only for the
        # named customers, which is how the shop actually works.
        discount = Decimal("0")
        if customer_id is not None and random.random() < 0.35:
            discount = money(float(subtotal) * random.uniform(0.02, 0.08))

        total = money(float(subtotal - discount))

        cursor.execute(
            "INSERT INTO bills (bill_no, cust_id, emp_id, bill_dt, total_amt, discount_amt, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING bill_id",
            (
                f"BILL-{bill_dt.strftime('%Y%m%d')}-{bill_number:05d}",
                customer_id,
                random.choice(cashier_ids),
                bill_dt,
                total,
                discount,
                status,
            ),
        )
        bill_id = cursor.fetchone()[0]

        cursor.executemany(
            "INSERT INTO bill_items (bill_id, prod_id, qty, price_at_sale, line_total) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(bill_id, product_id, qty, price, line_total)
             for product_id, qty, price, line_total in lines],
        )

        # Payments. A cancelled bill was never collected. A PARTIAL one
        # has had some money against it and still owes the rest, which is
        # what makes "billed but not collected" a real number rather than
        # always zero.
        #
        # I had this wrong at first: PARTIAL bills got two payments that
        # added up to the full total, so nothing was ever outstanding and
        # the eval question about uncollected money was answered
        # correctly by any query that happened to return 0. A test that
        # passes for the wrong reason is worse than no test.
        if status == "PAID":
            mode = random.choices(["CASH", "UPI", "CARD", "CREDIT"], weights=[45, 35, 12, 8])[0]
            # About one PAID bill in eight was settled in two goes, so
            # payments genuinely has more rows than bills and a naive
            # join multiplies the totals.
            if random.random() < 0.12:
                part = money(float(total) * random.uniform(0.4, 0.6))
                payments.append((bill_id, "CASH", part, bill_dt))
                payments.append((
                    bill_id, "UPI", money(float(total - part)),
                    bill_dt + timedelta(days=random.randint(3, 30)),
                ))
            else:
                payments.append((bill_id, mode, total, bill_dt))
        elif status == "PARTIAL":
            part = money(float(total) * random.uniform(0.3, 0.7))
            payments.append((bill_id, "CASH", part, bill_dt))

    cursor.executemany(
        "INSERT INTO payments (bill_id, pay_mode, amt, pay_dt) VALUES (%s, %s, %s, %s)",
        payments,
    )

    # --------------------------------------------------------- archive

    print("bill archive")

    archive = []
    for index in range(900):
        txn_dt = random_datetime(ARCHIVE_FROM, ARCHIVE_TO)
        # Plain text names, because the old system had no customer or
        # employee tables. There is nothing to join to and there never
        # will be.
        archive.append((
            index + 1,
            f"INV-{txn_dt.strftime('%Y%m%d')}-{index + 1:05d}",
            random.choice(CUSTOMER_NAMES) if random.random() < 0.35 else None,
            random.choice([name for name, role, *_ in EMPLOYEES if role != "OWNER"]),
            txn_dt,
            money(random.uniform(180, 14500)),
            random.choices(["PAID", "CANCELLED"], weights=[95, 5])[0],
        ))

    cursor.executemany(
        "INSERT INTO bill_archive (bill_id, bill_no, cust_name, emp_name, txn_dt, amt, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        archive,
    )

    # ----------------------------------------------------- adjustments

    print("stock adjustments and settings")

    adjustments = []
    for _ in range(70):
        reason = random.choices(["DAMAGE", "THEFT", "STOCKTAKE"], weights=[55, 15, 30])[0]
        if reason == "STOCKTAKE":
            # A stocktake can find more than the system thought, so this
            # one goes either way. SUM() over qty_change is therefore a
            # net figure and not a total loss.
            change = random.choice([-1, 1]) * random.randint(1, 15)
        else:
            change = -random.randint(1, 25)

        adjustments.append((
            random.choice(product_ids),
            change,
            reason,
            LIVE_FROM + timedelta(days=random.randint(0, (TODAY - LIVE_FROM).days)),
            random.choice(storekeeper_ids),
        ))

    cursor.executemany(
        "INSERT INTO stock_adjustments (prod_id, qty_change, reason, adj_dt, emp_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        adjustments,
    )

    cursor.executemany(
        "INSERT INTO store_settings (setting_key, setting_val) VALUES (%s, %s)",
        SETTINGS,
    )

    # ANALYZE so the row count estimates that describe_table shows are
    # real. Without it every table reports -1 and the model is told
    # nothing about how big anything is.
    connection.commit()
    connection.autocommit = True
    cursor.execute("ANALYZE")

    cursor.close()
    connection.close()
    print("\ndone")


if __name__ == "__main__":
    seed()
