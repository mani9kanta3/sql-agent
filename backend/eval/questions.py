"""
Forty questions with ground truth. This is the project's evidence.

The guide's warning is that the agent is fun and the eval is not, so the
eval never gets built and what is left is a text to SQL demo. I wrote
these before I was finished with the graph, for exactly that reason.

**Ground truth is a query, not an answer.** Two completely different
queries can both be correct, so grading on SQL text would fail a correct
answer for being written differently. Every serious text to SQL benchmark
grades on execution accuracy: run the agent's query and the reference
query, compare the result sets. That is what run_eval.py does.

**Ten of the forty cannot be answered.** The database does not hold
returns, attendance, ratings, warranties or web traffic. A model asked
about them will happily invent a column and write a query that runs. The
right answer is to refuse, and refusal accuracy is scored separately,
because an agent that is right about the thirty and confidently wrong
about the ten is not usable.

Written against the seeded database, which is reproducible because
scripts/seed.py fixes its random seed. Rebuild the database and these
stay correct.
"""

# Every question is:
#   id, category, question, expected_sql (None when unanswerable), note
#
# category is one of: simple, join, hard, unanswerable

QUESTIONS = [

    # ------------------------------------------------ simple aggregates

    {
        "id": 1,
        "category": "simple",
        "question": "How many products are in the catalogue?",
        "expected_sql": "SELECT COUNT(*) AS n FROM products",
        "note": "The easy one. It is here to catch a broken setup, not to be hard.",
    },
    {
        "id": 2,
        "category": "simple",
        "question": "How many bills were raised in July 2026?",
        "expected_sql": """
            SELECT COUNT(*) AS n
            FROM bills
            WHERE bill_dt >= DATE '2026-07-01'
              AND bill_dt < DATE '2026-08-01'
        """,
        "note": "bill_dt is a TIMESTAMP, so a plain equality on a date misses most of the day.",
    },
    {
        "id": 3,
        "category": "simple",
        "question": "What is our total sales value in 2026 so far, not counting cancelled bills?",
        "expected_sql": """
            SELECT SUM(total_amt) AS total
            FROM bills
            WHERE bill_dt >= DATE '2026-01-01'
              AND status <> 'CANCELLED'
        """,
        "note": "Status is upper case. 'cancelled' matches nothing and looks like a working query.",
    },
    {
        "id": 4,
        "category": "simple",
        "question": "How many suppliers are we still buying from?",
        "expected_sql": "SELECT COUNT(*) AS n FROM suppliers WHERE is_active = TRUE",
        "note": "",
    },
    {
        "id": 5,
        "category": "simple",
        "question": "Which products are at or below their reorder level?",
        # Only the names. The question asks which products, so the stock
        # and reorder figures are presentation, not part of the answer,
        # and the comparator lets the agent add them if it wants to.
        "expected_sql": """
            SELECT prod_name
            FROM products
            WHERE stock_qty <= reorder_lvl
            ORDER BY prod_name
        """,
        "note": "Two columns of the same row compared. Easy to write, easy to get backwards.",
    },
    {
        "id": 6,
        "category": "simple",
        "question": "What is the average bill value this year, ignoring cancelled bills?",
        "expected_sql": """
            SELECT AVG(total_amt) AS average
            FROM bills
            WHERE bill_dt >= DATE '2026-01-01'
              AND status <> 'CANCELLED'
        """,
        "note": "",
    },
    {
        "id": 7,
        "category": "simple",
        "question": "How many of our staff are cashiers?",
        "expected_sql": "SELECT COUNT(*) AS n FROM employees WHERE role = 'CASHIER'",
        "note": "",
    },
    {
        "id": 8,
        "category": "simple",
        "question": "What is our most expensive product?",
        "expected_sql": """
            SELECT prod_name, sell_price
            FROM products
            ORDER BY sell_price DESC
            LIMIT 1
        """,
        "note": "tbl_prod_master_old has a rate column and stale prices. Using it gives a wrong answer that runs.",
    },
    {
        "id": 9,
        "category": "simple",
        "question": "How many stock adjustments were recorded as theft?",
        "expected_sql": "SELECT COUNT(*) AS n FROM stock_adjustments WHERE reason = 'THEFT'",
        "note": "",
    },
    {
        "id": 10,
        "category": "simple",
        "question": "How many of our customers buy on credit?",
        "expected_sql": "SELECT COUNT(*) AS n FROM customers WHERE is_credit_customer = TRUE",
        "note": "",
    },

    # ------------------------------------------------------------ joins

    {
        "id": 11,
        "category": "join",
        "question": "Which product has sold the most units in 2026?",
        "expected_sql": """
            SELECT p.prod_name, SUM(bi.qty) AS units
            FROM bill_items bi
            JOIN bills b ON b.bill_id = bi.bill_id
            JOIN products p ON p.prod_id = bi.prod_id
            WHERE b.bill_dt >= DATE '2026-01-01'
              AND b.status <> 'CANCELLED'
            GROUP BY p.prod_name
            ORDER BY units DESC
            LIMIT 1
        """,
        "note": "Quantities live on bill_items, dates live on bills. Both tables are needed.",
    },
    {
        "id": 12,
        "category": "join",
        "question": "Which supplier have we spent the most with on goods received?",
        "expected_sql": """
            SELECT s.supp_name, SUM(se.qty * se.cost_price) AS spend
            FROM stock_entries se
            JOIN suppliers s ON s.supp_id = se.supp_id
            GROUP BY s.supp_name
            ORDER BY spend DESC
            LIMIT 1
        """,
        "note": "Spend is qty times cost_price. Summing cost_price alone is a very easy mistake.",
    },
    {
        "id": 13,
        "category": "join",
        "question": "Which cashier has billed the highest total value in 2026?",
        "expected_sql": """
            SELECT e.emp_name, SUM(b.total_amt) AS billed
            FROM bills b
            JOIN employees e ON e.emp_id = b.emp_id
            WHERE b.bill_dt >= DATE '2026-01-01'
              AND b.status <> 'CANCELLED'
            GROUP BY e.emp_name
            ORDER BY billed DESC
            LIMIT 1
        """,
        "note": "",
    },
    {
        "id": 14,
        "category": "join",
        "question": "What is the total sales value for each product category in 2026?",
        "expected_sql": """
            SELECT c.cat_name, SUM(bi.line_total) AS total
            FROM bill_items bi
            JOIN bills b ON b.bill_id = bi.bill_id
            JOIN products p ON p.prod_id = bi.prod_id
            JOIN categories c ON c.cat_id = p.cat_id
            WHERE b.bill_dt >= DATE '2026-01-01'
              AND b.status <> 'CANCELLED'
            GROUP BY c.cat_name
            ORDER BY total DESC
        """,
        "note": "Four tables. This is where an unqualified column starts throwing ambiguous_column.",
    },
    {
        "id": 15,
        "category": "join",
        "question": "Who are our top five customers by amount billed?",
        "expected_sql": """
            SELECT c.cust_name, SUM(b.total_amt) AS billed
            FROM bills b
            JOIN customers c ON c.cust_id = b.cust_id
            WHERE b.status <> 'CANCELLED'
            GROUP BY c.cust_name
            ORDER BY billed DESC
            LIMIT 5
        """,
        "note": "An inner join is correct here because walk in bills have no customer to rank.",
    },
    {
        "id": 16,
        "category": "join",
        "question": "How much money have we collected by each payment mode in 2026?",
        "expected_sql": """
            SELECT pay_mode, SUM(amt) AS collected
            FROM payments
            WHERE pay_dt >= DATE '2026-01-01'
            GROUP BY pay_mode
            ORDER BY collected DESC
        """,
        "note": "Collected is payments, not bills. Answering from bills gives a different number.",
    },
    {
        "id": 17,
        "category": "join",
        "question": "Which purchase orders have not been fully received?",
        # The order id identifies the order. Supplier and status are
        # useful to show and are not what was asked for.
        "expected_sql": """
            SELECT DISTINCT po.po_id
            FROM po_lines pl
            JOIN purchase_orders po ON po.po_id = pl.po_id
            WHERE pl.qty_received < pl.qty_ordered
              AND po.status <> 'CANCELLED'
            ORDER BY po.po_id
        """,
        "note": "",
    },
    {
        "id": 18,
        "category": "join",
        "question": "Which products have never been sold?",
        "expected_sql": """
            SELECT p.prod_name
            FROM products p
            LEFT JOIN bill_items bi ON bi.prod_id = p.prod_id
            WHERE bi.item_id IS NULL
            ORDER BY p.prod_name
        """,
        "note": "Every product has sold in the seeded data, so the correct answer is no rows. "
                "This is the empty result path, and answering 'none' is right.",
    },
    {
        "id": 19,
        "category": "join",
        "question": "How many units have we lost to damaged stock, by product?",
        "expected_sql": """
            SELECT p.prod_name, SUM(ABS(sa.qty_change)) AS lost
            FROM stock_adjustments sa
            JOIN products p ON p.prod_id = sa.prod_id
            WHERE sa.reason = 'DAMAGE'
            GROUP BY p.prod_name
            ORDER BY lost DESC
        """,
        "note": "qty_change is negative for a loss, so a bare SUM returns a negative 'loss'.",
    },
    {
        "id": 20,
        "category": "join",
        "question": "Which employees report to the owner?",
        "expected_sql": """
            SELECT e.emp_name
            FROM employees e
            JOIN employees m ON m.emp_id = e.mgr_id
            WHERE m.role = 'OWNER'
            ORDER BY e.emp_name
        """,
        "note": "A self join on the same table, which models get wrong more often than a normal join.",
    },

    # ---------------------------------------------------- hard/ambiguous

    {
        "id": 21,
        "category": "hard",
        "question": "What was our total revenue in 2023?",
        "expected_sql": """
            SELECT SUM(amt) AS total
            FROM bill_archive
            WHERE txn_dt >= DATE '2023-01-01'
              AND txn_dt < DATE '2024-01-01'
              AND status <> 'CANCELLED'
        """,
        "note": "The trap of the whole set. bills starts in 2025, so querying it returns "
                "zero and looks like a real answer. 2023 is only in bill_archive.",
    },
    {
        "id": 22,
        "category": "hard",
        "question": "What is our total revenue across the whole history of the shop?",
        "expected_sql": """
            SELECT (
                (SELECT COALESCE(SUM(total_amt), 0) FROM bills WHERE status <> 'CANCELLED')
              + (SELECT COALESCE(SUM(amt), 0) FROM bill_archive WHERE status <> 'CANCELLED')
            ) AS total
        """,
        "note": "Needs both tables and needs to notice that they are the same thing "
                "with different column names.",
    },
    {
        "id": 23,
        "category": "hard",
        "question": "Show me the sales total for each month of 2026.",
        "expected_sql": """
            SELECT DATE_TRUNC('month', bill_dt) AS month, SUM(total_amt) AS total
            FROM bills
            WHERE bill_dt >= DATE '2026-01-01'
              AND status <> 'CANCELLED'
            GROUP BY DATE_TRUNC('month', bill_dt)
            ORDER BY month
        """,
        "note": "",
    },
    {
        "id": 24,
        "category": "hard",
        "question": "Which product has the biggest margin between its selling price and its average cost price?",
        "expected_sql": """
            SELECT p.prod_name,
                   p.sell_price - AVG(se.cost_price) AS margin
            FROM products p
            JOIN stock_entries se ON se.prod_id = p.prod_id
            GROUP BY p.prod_id, p.prod_name, p.sell_price
            ORDER BY margin DESC
            LIMIT 1
        """,
        "note": "sell_price is on products, cost_price is on stock_entries. Two tables "
                "for one number, and the grouping has to carry sell_price through.",
    },
    {
        "id": 25,
        "category": "hard",
        "question": "Give me the top three products by sales value within each category in 2026.",
        "expected_sql": """
            SELECT cat_name, prod_name, revenue
            FROM (
                SELECT c.cat_name,
                       p.prod_name,
                       SUM(bi.line_total) AS revenue,
                       ROW_NUMBER() OVER (
                           PARTITION BY c.cat_name ORDER BY SUM(bi.line_total) DESC
                       ) AS rank_in_cat
                FROM bill_items bi
                JOIN bills b ON b.bill_id = bi.bill_id
                JOIN products p ON p.prod_id = bi.prod_id
                JOIN categories c ON c.cat_id = p.cat_id
                WHERE b.bill_dt >= DATE '2026-01-01'
                  AND b.status <> 'CANCELLED'
                GROUP BY c.cat_name, p.prod_name
            ) ranked
            WHERE rank_in_cat <= 3
            ORDER BY cat_name, revenue DESC
        """,
        "note": "A window function over an aggregate, which needs a subquery because "
                "you cannot filter on a window function in the same WHERE.",
    },
    {
        "id": 26,
        "category": "hard",
        "question": "Which day of the week do we take the most money?",
        "expected_sql": """
            SELECT EXTRACT(DOW FROM bill_dt) AS day_of_week, SUM(total_amt) AS total
            FROM bills
            WHERE status <> 'CANCELLED'
            GROUP BY EXTRACT(DOW FROM bill_dt)
            ORDER BY total DESC
            LIMIT 1
        """,
        "note": "PostgreSQL date functions, not MySQL's. DAYOFWEEK() does not exist here "
                "and comes back as unknown_function.",
    },
    {
        "id": 27,
        "category": "hard",
        "question": "How much have we billed in 2026 that has not been collected yet?",
        "expected_sql": """
            SELECT SUM(b.total_amt) - COALESCE(SUM(paid.received), 0) AS outstanding
            FROM bills b
            LEFT JOIN (
                SELECT bill_id, SUM(amt) AS received
                FROM payments
                GROUP BY bill_id
            ) paid ON paid.bill_id = b.bill_id
            WHERE b.bill_dt >= DATE '2026-01-01'
              AND b.status <> 'CANCELLED'
        """,
        "note": "Payments has more than one row per bill, so joining it directly "
                "multiplies the bill total. It has to be aggregated first.",
    },
    {
        "id": 28,
        "category": "hard",
        "question": "Which customers have not bought anything in the last six months?",
        "expected_sql": """
            SELECT c.cust_name
            FROM customers c
            WHERE NOT EXISTS (
                SELECT 1
                FROM bills b
                WHERE b.cust_id = c.cust_id
                  AND b.bill_dt >= CURRENT_DATE - INTERVAL '6 months'
            )
            ORDER BY c.cust_name
        """,
        "note": "An absence question. A plain join cannot express it.",
    },
    {
        "id": 29,
        "category": "hard",
        "question": "What percentage of our bills are to walk-in customers rather than named ones?",
        "expected_sql": """
            SELECT ROUND(
                100.0 * COUNT(*) FILTER (WHERE cust_id IS NULL) / COUNT(*), 2
            ) AS walk_in_percent
            FROM bills
        """,
        "note": "The nullable foreign key made explicit. Counting cust_id instead of * "
                "skips the nulls and gives 100 percent.",
    },
    {
        "id": 30,
        "category": "hard",
        "question": "For each supplier, how many ordered units are still not received?",
        "expected_sql": """
            SELECT s.supp_name,
                   SUM(pl.qty_ordered - pl.qty_received) AS pending
            FROM po_lines pl
            JOIN purchase_orders po ON po.po_id = pl.po_id
            JOIN suppliers s ON s.supp_id = po.supp_id
            WHERE po.status <> 'CANCELLED'
            GROUP BY s.supp_name
            HAVING SUM(pl.qty_ordered - pl.qty_received) > 0
            ORDER BY pending DESC
        """,
        "note": "HAVING rather than WHERE, because the filter is on the aggregate.",
    },

    # ----------------------------------------------------- unanswerable
    #
    # None of these are in the schema. The correct behaviour is to say so.
    # A model that wants to be helpful will invent returns_count or a
    # rating column and write a query that runs perfectly.

    {
        "id": 31,
        "category": "unanswerable",
        "question": "Which products get returned by customers most often?",
        "expected_sql": None,
        "note": "There is no returns table. Sales are recorded, reversals are not.",
    },
    {
        "id": 32,
        "category": "unanswerable",
        "question": "What was each employee's attendance last month?",
        "expected_sql": None,
        "note": "employees holds name, role, joining date and salary. No attendance.",
    },
    {
        "id": 33,
        "category": "unanswerable",
        "question": "How many people visited our website last week?",
        "expected_sql": None,
        "note": "This is a shop database. There is no web traffic in it.",
    },
    {
        "id": 34,
        "category": "unanswerable",
        "question": "What is the warranty period on the drill machine?",
        "expected_sql": None,
        "note": "products has price, unit and stock. Warranty is not modelled.",
    },
    {
        "id": 35,
        "category": "unanswerable",
        "question": "Which supplier has the best delivery rating?",
        "expected_sql": None,
        "note": "Tempting, because purchase_orders has dates. But there is no rating, "
                "and no actual delivery date to compute one from either.",
    },
    {
        "id": 36,
        "category": "unanswerable",
        "question": "How much do we pay in shop rent every month?",
        "expected_sql": None,
        "note": "Expenses are not in this database at all. Only stock and sales.",
    },
    {
        "id": 37,
        "category": "unanswerable",
        "question": "Which customers have agreed to receive marketing messages?",
        "expected_sql": None,
        "note": "customers has a phone number but no consent field.",
    },
    {
        "id": 38,
        "category": "unanswerable",
        "question": "What was the weather like on our busiest sales day?",
        "expected_sql": None,
        "note": "The busiest day is answerable. The weather is not, so the whole "
                "question is not, and half answering it would be worse.",
    },
    {
        "id": 39,
        "category": "unanswerable",
        "question": "How many hours did each cashier work last week?",
        "expected_sql": None,
        "note": "No shifts, no clock in. Bills have timestamps, but the first and "
                "last bill of a day is not the same as hours worked.",
    },
    {
        "id": 40,
        "category": "unanswerable",
        "question": "Are we on track to hit this year's profit target?",
        "expected_sql": None,
        "note": "There is no targets table. Revenue is known, the target is not.",
    },
]


def by_category(name):
    return [question for question in QUESTIONS if question["category"] == name]


def answerable():
    return [question for question in QUESTIONS if question["expected_sql"]]


def unanswerable():
    return [question for question in QUESTIONS if not question["expected_sql"]]


if __name__ == "__main__":
    for category in ["simple", "join", "hard", "unanswerable"]:
        print(f"{category:14} {len(by_category(category))}")
    print(f"{'total':14} {len(QUESTIONS)}")
