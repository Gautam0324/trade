# PHASE 3.2 -- RECOVERY & DISASTER RESTORATION PROCEDURES

## Overview

This report documents the failure modes, automated recovery mechanisms, and operator runbooks implemented in Phase 3.2 for execution reliability and state restoration.

---

## 1. Automated Failure Recovery Mechanisms

### A. Process Restart During In-Flight Order (`recover_pending_orders`)
- **Failure Mode:**
  The Python process crashes (e.g. OOM, power loss, or OS restart) while an order was dispatched but before fill confirmation was received.
- **Automated Recovery Protocol:**
  1. On trader startup, `recover_pending_orders(broker_adapter)` scans the `orders` table for orders in `CREATED`, `VALIDATED`, or `SUBMITTED`.
  2. **Rule: Never Assume Filled.**
  3. If broker adapter is connected and reports `status == 'COMPLETE'`, the system reconciles the actual fill price and opens the position.
  4. If broker reports `REJECTED` or `CANCELLED`, order status is transitioned to `CANCELLED`.
  5. If broker state is uncertain or in paper simulation mode:
     The order is immediately transitioned to `REQUIRES_OPERATOR_REVIEW`.
     Log: `[ORDER RECOVERY] Order {order_id} flagged for operator review. Execution status uncertain.`
  6. While an order remains in `REQUIRES_OPERATOR_REVIEW`, `can_admit_order` blocks new submissions for that symbol, preventing duplicate order submission.

### B. Orphaned Order Reconciliation (`reconcile_orphaned_orders`)
- **Failure Mode:**
  An order was transitioned to `FILLED` in SQLite, but the process was interrupted before the corresponding position was written to `positions` or synced to the ledger.
- **Automated Recovery Protocol:**
  1. Queries all orders where `status == 'FILLED'` that lack a corresponding record in `positions`.
  2. Automatically reconstructs the open position record using the order's contract details, quantity, and fill price.
  3. Re-syncs the open position to the ledger under `locked_ledger_transaction`.

### C. Partial Fill Recovery (`record_partial_fill`)
- **Failure Mode:**
  A 65-share order receives a partial fill of 30 shares followed by a network interruption.
- **Automated Recovery Protocol:**
  1. First fill records `filled_quantity = 30` and sets order status to `PARTIALLY_FILLED`.
  2. Creates an open position for 30 shares.
  3. Subsequent fill for remaining 35 shares calculates volume-weighted average price (VWAP) on the position, updates total quantity to 65 shares, and transitions order status to `FILLED`.

### D. Corrupt Ledger Auto-Healing
- **Failure Mode:**
  Unexpected disk corruption, truncated write, or invalid JSON syntax in a ledger file.
- **Automated Recovery Protocol:**
  `read_ledger_locked()` detects `JSONDecodeError`. It automatically retrieves the latest valid snapshot from `ledger.backup`, logs the healing event, and atomically restores the ledger.

---

## 2. Operator Manual Review Runbook

When an order enters `REQUIRES_OPERATOR_REVIEW`:

1. **Identify Flagged Orders:**
   Inspect SQLite orders table:
   ```powershell
   python -c "
   import sqlite3
   conn = sqlite3.connect('trader/orders_lifecycle.db')
   conn.row_factory = sqlite3.Row
   for r in conn.execute('SELECT * FROM orders WHERE status = \'REQUIRES_OPERATOR_REVIEW\'').fetchall():
       print(dict(r))
   "
   ```

2. **Verify Broker Terminal State:**
   Log into the Angel One terminal and check the Order Book for the contract trading symbol.
   - If the order was **FILLED on the exchange**:
     Manually confirm the fill in SQLite:
     ```sql
     UPDATE orders SET status = 'FILLED', updated_at = datetime('now') WHERE order_id = '<ORDER_ID>';
     ```
   - If the order was **REJECTED or NEVER PLACED**:
     Cancel the order in SQLite:
     ```sql
     UPDATE orders SET status = 'CANCELLED', rejection_reason = 'Operator verified not placed', updated_at = datetime('now') WHERE order_id = '<ORDER_ID>';
     ```

3. **Resume Automated Trading:**
   Once the status is updated away from `REQUIRES_OPERATOR_REVIEW`, the symbol is immediately unlocked for future trading signals.

