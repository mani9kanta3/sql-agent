-- The database the agent has to answer questions about.
--
-- This is my hardware store project's schema, grown into what a real
-- shop's database looks like after a few years of people adding things
-- in a hurry. The guide is firm about this: a clean schema makes a
-- boring agent, because the interesting failures never happen.
--
-- The mess in here is on purpose, and each piece of it is a specific
-- failure I want the agent to hit:
--
--   * short column names (supp_id, txn_dt, amt) so the model has to
--     read the DDL instead of guessing English names
--   * "status" on three different tables, so an unqualified column in
--     a join comes back as an ambiguous_column error
--   * bills.cust_id is nullable, because a walk in customer has no
--     record. Any per customer total that uses an inner join quietly
--     loses every cash sale
--   * bill_archive holds the old bills and is nearly the same shape as
--     bills, with different column names. "Revenue in 2023" is wrong
--     unless the archive is included
--   * tbl_prod_master_old is a dead table nobody deleted. It looks
--     like products and is not. Joining it is a real mistake
--   * status values are stored uppercase ('PAID', not 'paid'), which
--     is exactly the sort of thing that returns zero rows and looks
--     like a working query

DROP TABLE IF EXISTS stock_adjustments CASCADE;
DROP TABLE IF EXISTS po_lines CASCADE;
DROP TABLE IF EXISTS purchase_orders CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS bill_items CASCADE;
DROP TABLE IF EXISTS bills CASCADE;
DROP TABLE IF EXISTS bill_archive CASCADE;
DROP TABLE IF EXISTS stock_entries CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS tbl_prod_master_old CASCADE;
DROP TABLE IF EXISTS categories CASCADE;
DROP TABLE IF EXISTS suppliers CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS employees CASCADE;
DROP TABLE IF EXISTS store_settings CASCADE;


-- ---------------------------------------------------------- reference

CREATE TABLE categories (
    cat_id      SERIAL PRIMARY KEY,
    cat_name    VARCHAR(60) NOT NULL UNIQUE
);

CREATE TABLE suppliers (
    supp_id     SERIAL PRIMARY KEY,
    supp_name   VARCHAR(100) NOT NULL,
    phone_no    VARCHAR(15),
    addr        TEXT,
    -- Nullable because the small local suppliers are not registered.
    -- A question about GST registered suppliers has to cope with that.
    gst_no      VARCHAR(20),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE employees (
    emp_id      SERIAL PRIMARY KEY,
    emp_name    VARCHAR(100) NOT NULL,
    role        VARCHAR(20) NOT NULL,          -- 'OWNER', 'CASHIER', 'STOREKEEPER'
    join_dt     DATE NOT NULL,
    -- Self reference. The owner reports to nobody, so this is null for
    -- exactly one row, and a self join has to be a LEFT JOIN.
    mgr_id      INTEGER REFERENCES employees(emp_id),
    salary      NUMERIC(10, 2)
);

CREATE TABLE customers (
    cust_id             SERIAL PRIMARY KEY,
    cust_name           VARCHAR(100) NOT NULL,
    phone_no            VARCHAR(15),
    addr                TEXT,
    -- Contractors buy on credit and settle at month end.
    is_credit_customer  BOOLEAN NOT NULL DEFAULT FALSE,
    created_dt          DATE NOT NULL DEFAULT CURRENT_DATE
);


-- ------------------------------------------------------------ catalogue

CREATE TABLE products (
    prod_id      SERIAL PRIMARY KEY,
    prod_name    VARCHAR(120) NOT NULL,
    sku          VARCHAR(40) NOT NULL UNIQUE,
    unit         VARCHAR(10) NOT NULL,         -- 'piece', 'kg', 'litre', 'metre', 'box'
    sell_price   NUMERIC(10, 2) NOT NULL,
    stock_qty    INTEGER NOT NULL DEFAULT 0,
    reorder_lvl  INTEGER NOT NULL DEFAULT 10,
    cat_id       INTEGER NOT NULL REFERENCES categories(cat_id),
    created_dt   DATE NOT NULL DEFAULT CURRENT_DATE
);

-- The trap. This was the product list before the current system, and
-- nobody deleted it. It holds a stale subset with different names and
-- no stock. If the agent joins this instead of products, the query runs
-- perfectly and the answer is wrong, which is the worst kind of bug.
CREATE TABLE tbl_prod_master_old (
    p_id         INTEGER PRIMARY KEY,
    p_name       VARCHAR(120),
    p_code       VARCHAR(40),
    rate         NUMERIC(10, 2),
    active_flag  CHAR(1)                       -- 'Y' or 'N'
);


-- --------------------------------------------------------- goods inward

CREATE TABLE stock_entries (
    entry_id    SERIAL PRIMARY KEY,
    prod_id     INTEGER NOT NULL REFERENCES products(prod_id),
    supp_id     INTEGER NOT NULL REFERENCES suppliers(supp_id),
    qty         INTEGER NOT NULL,
    cost_price  NUMERIC(10, 2) NOT NULL,       -- what we paid, not what we sell for
    recv_dt     TIMESTAMP NOT NULL,
    recv_by     INTEGER REFERENCES employees(emp_id)
);

CREATE TABLE purchase_orders (
    po_id        SERIAL PRIMARY KEY,
    supp_id      INTEGER NOT NULL REFERENCES suppliers(supp_id),
    po_dt        DATE NOT NULL,
    -- Null when the supplier never confirmed a date.
    expected_dt  DATE,
    status       VARCHAR(15) NOT NULL,         -- 'OPEN', 'PARTIAL', 'CLOSED', 'CANCELLED'
    total_amt    NUMERIC(12, 2) NOT NULL DEFAULT 0
);

CREATE TABLE po_lines (
    po_line_id   SERIAL PRIMARY KEY,
    po_id        INTEGER NOT NULL REFERENCES purchase_orders(po_id),
    prod_id      INTEGER NOT NULL REFERENCES products(prod_id),
    qty_ordered  INTEGER NOT NULL,
    qty_received INTEGER NOT NULL DEFAULT 0,
    unit_cost    NUMERIC(10, 2) NOT NULL
);


-- ------------------------------------------------------------- selling

CREATE TABLE bills (
    bill_id       SERIAL PRIMARY KEY,
    bill_no       VARCHAR(20) NOT NULL UNIQUE,
    -- Null for a walk in customer paying cash, which is most sales.
    -- Any "revenue per customer" query that uses an inner join silently
    -- drops the majority of the shop's takings.
    cust_id       INTEGER REFERENCES customers(cust_id),
    emp_id        INTEGER NOT NULL REFERENCES employees(emp_id),
    bill_dt       TIMESTAMP NOT NULL,
    total_amt     NUMERIC(12, 2) NOT NULL,
    discount_amt  NUMERIC(10, 2) NOT NULL DEFAULT 0,
    status        VARCHAR(12) NOT NULL          -- 'PAID', 'PARTIAL', 'CANCELLED'
);

CREATE TABLE bill_items (
    item_id        SERIAL PRIMARY KEY,
    bill_id        INTEGER NOT NULL REFERENCES bills(bill_id),
    prod_id        INTEGER NOT NULL REFERENCES products(prod_id),
    qty            INTEGER NOT NULL,
    -- Frozen at the time of sale, so an old bill stays correct after a
    -- price change. Same decision as the hardware store project.
    price_at_sale  NUMERIC(10, 2) NOT NULL,
    line_total     NUMERIC(12, 2) NOT NULL
);

-- Bills from before the current system. Same idea, different names, and
-- the customer and employee are plain text because the old system had
-- no tables for them. There is no foreign key here and there cannot be.
CREATE TABLE bill_archive (
    bill_id     INTEGER PRIMARY KEY,
    bill_no     VARCHAR(20) NOT NULL,
    cust_name   VARCHAR(100),
    emp_name    VARCHAR(100),
    txn_dt      TIMESTAMP NOT NULL,
    amt         NUMERIC(12, 2) NOT NULL,
    status      VARCHAR(12) NOT NULL
);

-- One bill can be settled in two parts, so this is not one row per bill.
-- Summing payments and summing bills are different numbers, and a
-- question about "money collected" means this table, not bills.
CREATE TABLE payments (
    pay_id    SERIAL PRIMARY KEY,
    bill_id   INTEGER NOT NULL REFERENCES bills(bill_id),
    pay_mode  VARCHAR(10) NOT NULL,             -- 'CASH', 'UPI', 'CARD', 'CREDIT'
    amt       NUMERIC(12, 2) NOT NULL,
    pay_dt    TIMESTAMP NOT NULL
);


-- ---------------------------------------------------------- corrections

CREATE TABLE stock_adjustments (
    adj_id      SERIAL PRIMARY KEY,
    prod_id     INTEGER NOT NULL REFERENCES products(prod_id),
    -- Negative for damage and theft, positive when a stocktake finds
    -- more than the system thought. SUM() over this is not a count.
    qty_change  INTEGER NOT NULL,
    reason      VARCHAR(20) NOT NULL,           -- 'DAMAGE', 'THEFT', 'STOCKTAKE'
    adj_dt      DATE NOT NULL,
    emp_id      INTEGER REFERENCES employees(emp_id)
);

-- Key value config. Nothing in here answers a business question, so a
-- good schema retriever should never pick it. It is here because every
-- real database has one and it is a fair distraction.
CREATE TABLE store_settings (
    setting_key  VARCHAR(50) PRIMARY KEY,
    setting_val  TEXT
);


-- Indexes on the columns the agent will filter and join on most.
CREATE INDEX idx_bills_bill_dt ON bills(bill_dt);
CREATE INDEX idx_bills_cust_id ON bills(cust_id);
CREATE INDEX idx_bill_items_bill_id ON bill_items(bill_id);
CREATE INDEX idx_bill_items_prod_id ON bill_items(prod_id);
CREATE INDEX idx_stock_entries_recv_dt ON stock_entries(recv_dt);
CREATE INDEX idx_payments_bill_id ON payments(bill_id);
CREATE INDEX idx_bill_archive_txn_dt ON bill_archive(txn_dt);
