"""
One short description per table, written by hand.

These do two jobs. They are what list_tables() returns, and they are
what gets embedded so the agent can pick the three to five tables a
question actually needs.

Why written by hand and not generated from the DDL. The column names in
this database are things like txn_dt and amt. Generating a description
from those gives back the same abbreviations the model was already
struggling with. The useful sentence is the one that says what the table
is *for* and, more importantly, what it is not for. "bill_archive holds
bills from before 2025" is the sentence that stops the agent answering a
question about last year from an empty table.

Each description ends with the trap, where there is one. That is
deliberate: the retriever matches on the whole sentence, so mentioning
"old bills, historical, archive, 2023, 2024" in the archive description
is what makes a question about 2023 pull the right table.
"""

TABLE_NOTES = {
    "categories": (
        "Product categories such as Plumbing, Electrical, Fasteners, Paint and "
        "Tools. Only an id and a name. Join to products on cat_id to group or "
        "filter sales by category."
    ),
    "suppliers": (
        "The businesses the shop buys stock from. Name, phone, address and GST "
        "number. gst_no is null for the small unregistered suppliers, so a "
        "count of registered suppliers must exclude nulls. is_active is false "
        "for suppliers no longer used."
    ),
    "employees": (
        "Shop staff: cashiers, storekeepers and the owner. Name, role (OWNER, "
        "CASHIER or STOREKEEPER), joining date, salary, and mgr_id pointing at "
        "their manager in this same table. Join to bills on emp_id to find which "
        "cashier or staff member made a sale. The owner's mgr_id is null, so a "
        "manager join has to be a LEFT JOIN or the owner disappears."
    ),
    "customers": (
        "Named customers, mostly contractors who buy on credit. "
        "is_credit_customer marks them. Most sales are to walk in customers who "
        "are not in this table at all, so this is not a list of everyone who "
        "ever bought something."
    ),
    "products": (
        "The live product catalogue: name, sku, unit of measure, current selling "
        "price, stock on hand and reorder level. This is the correct products "
        "table. Stock is low or needs reordering when stock_qty is at or below "
        "reorder_lvl. This table says what a product is and what it costs today. "
        "It does not record any sale, so how many units sold is in bill_items."
    ),
    "tbl_prod_master_old": (
        "Dead legacy table from the shop's previous system. A stale partial copy "
        "of the product list with different column names and no stock figures. "
        "Nothing current is in here and nothing joins to it. Never use this to "
        "answer a question about products, prices or stock. Use products."
    ),
    "stock_entries": (
        "Goods received into the shop. One row each time a quantity of a product "
        "arrives from a supplier, with the cost_price paid and the date. This is "
        "how stock goes up. cost_price is what the shop paid, which is different "
        "from the selling price on products, so margin needs both."
    ),
    "purchase_orders": (
        "Orders placed with suppliers, with a date, an expected delivery date "
        "and a status of OPEN, PARTIAL, CLOSED or CANCELLED. An order is a "
        "request. It is not the same as goods actually received, which is "
        "stock_entries. expected_dt is null when the supplier never confirmed."
    ),
    "po_lines": (
        "The individual lines of a purchase order: which product, how many were "
        "ordered, how many have actually arrived, and the agreed unit cost. A "
        "line is short when qty_received is less than qty_ordered."
    ),
    "bills": (
        "Sales, invoices and revenue on the current system, from 2025 onward. "
        "One row per sale, with the bill number, the date and time, the total "
        "amount, any discount, the cashier who made it and the status (PAID, "
        "PARTIAL or CANCELLED). Turnover, daily and monthly sales, average bill "
        "value and sales per cashier all come from here. cust_id is null for "
        "walk in customers, which is most bills, so joining to customers with an "
        "inner join throws away the majority of sales. Cancelled bills should "
        "normally be excluded from revenue. This table has the totals; the "
        "individual products on each sale are in bill_items."
    ),
    "bill_items": (
        "What was sold. One row per product on a bill, with the quantity sold, "
        "the price it sold at and the line total. Units sold, quantity sold, "
        "best selling and worst selling products, sales per product, sales per "
        "category and revenue per item all come from here. Join to bills for "
        "the date and to products for the name. price_at_sale is frozen at the "
        "moment of sale and is not the product's current price."
    ),
    "bill_archive": (
        "Older sales from the shop's previous system, covering 2023 and 2024. "
        "Same idea as bills but flattened: the customer and employee are plain "
        "text names rather than ids, the date column is txn_dt and the total is "
        "amt. There are no line items for these bills. Any question about an "
        "earlier year, or about revenue across the shop's whole history, needs "
        "this table as well as bills."
    ),
    "payments": (
        "Money actually collected against a bill, with the mode (CASH, UPI, CARD "
        "or CREDIT) and the date. A bill can be settled in more than one payment, "
        "so this is not one row per bill. Cash collected is this table; sales "
        "billed is bills. They are different numbers."
    ),
    "stock_adjustments": (
        "Corrections to stock that are not a sale or a receipt: damage, theft, "
        "and stocktake differences. qty_change is negative for a loss and "
        "positive when a stocktake finds extra, so summing it gives a net figure "
        "and not a total loss."
    ),
    "store_settings": (
        "Key and value configuration for the till software, such as the shop "
        "name and receipt footer. Nothing here answers any business question "
        "about sales, stock, staff or suppliers."
    ),
}


# Values that are stored in a particular shape and would return zero rows
# if guessed wrong. The sample rows teach most of this, but the status
# ones are worth stating outright because a status filter that silently
# matches nothing is the failure that looks most like a working query.
VALUE_NOTES = """
Values are stored in upper case: bills.status is 'PAID', 'PARTIAL' or
'CANCELLED'. purchase_orders.status is 'OPEN', 'PARTIAL', 'CLOSED' or
'CANCELLED'. payments.pay_mode is 'CASH', 'UPI', 'CARD' or 'CREDIT'.
stock_adjustments.reason is 'DAMAGE', 'THEFT' or 'STOCKTAKE'.
employees.role is 'OWNER', 'CASHIER' or 'STOREKEEPER'.
products.unit is lower case: 'piece', 'kg', 'litre', 'metre', 'box'.
Money is in Indian rupees.

How this shop counts things:

- Revenue excludes CANCELLED bills only. A PARTIAL bill is a real sale
  that is part paid, so it counts in full. Use status <> 'CANCELLED',
  not status = 'PAID', which undercounts.
- Sales, revenue, turnover, billed and takings all mean bills and
  bill_items. Collected, received and paid mean payments. Different
  numbers: a bill can be raised one month and settled the next.
- Answer from bills unless the question reaches back before 2025 or asks
  about the whole history; only then add bill_archive.
"""

# Those three lines are business rules, not hints, and they are here
# because the evaluation showed I had never written them down anywhere.
#
# The agent kept answering revenue questions with status = 'PAID', which
# quietly drops every part paid bill. My ground truth used
# status <> 'CANCELLED'. Both are defensible readings of "revenue" and
# the schema does not say which the shop means, so the model was being
# marked wrong for guessing differently from me rather than for writing
# bad SQL.
#
# The fix is not to reword the questions, it is to write the convention
# down once, in the same place as the rest of the schema's tribal
# knowledge. That is what a real data team does, and it is the honest
# version of the fix because it applies to every question at once
# instead of only the ones my agent failed.
#
# Worth noting that these conventions do not all favour the agent. The
# scope rule sends "top five customers" to bills alone, which is the
# opposite of what the agent chose when it unioned in the archive.


def note_for(table_name):
    """The description for one table, or a plain fallback if it is new."""
    return TABLE_NOTES.get(table_name, "No description written for this table yet.")
