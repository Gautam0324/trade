# PHASE 3.2 -- PERSISTENCE & CONCURRENCY AUDIT REPORT

## 1. Persistence Layer Architecture

The Day Trader Pro persistence architecture utilizes a dual-tier storage strategy designed to balance high-speed in-memory execution with crash-resilient persistence:

```
                    Trading Engines (Cash & F&O)
                                 |
                 +---------------+---------------+
                 |                               |
        [ACID SQLite Layer]             [JSON Ledger Layer]
        trader/orders_lifecycle.db      trader/ledger*.json
                 |                               |
       WAL Journal Mode + Indices       FileLock + locked_ledger_transaction
       - orders table                   - ledger.json (Primary)
       - positions table                - ledger_fno.json (F&O Desk)
       - Unique signal_id               - ledger_himanshu.json (Sub-account)
       - In-flight lifecycle state      - ledger_prateek.json (Sub-account)
```

---

## 2. Order Lifecycle Database Audit (`orders_lifecycle.db`)

### A. Schema Verification
```sql
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    signal_id TEXT UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    filled_quantity INTEGER DEFAULT 0,
    price REAL NOT NULL,
    stop_loss REAL,
    target_price REAL,
    status TEXT NOT NULL,
    rejection_reason TEXT,
    strategy_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE positions (
    position_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    sl_price REAL NOT NULL,
    target_price REAL NOT NULL,
    entry_time TEXT NOT NULL,
    exit_price REAL,
    exit_time TEXT,
    gross_pnl REAL,
    fees REAL,
    net_pnl REAL,
    exit_reason TEXT,
    status TEXT NOT NULL, -- OPEN or CLOSED
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);
```

### B. Index & Performance Configuration
- `PRAGMA journal_mode=WAL;` enables non-blocking concurrent reads during writes.
- `idx_orders_sym_status` on `orders (symbol, status)` ensures sub-millisecond duplicate checks.
- `idx_positions_sym_status` on `positions (symbol, status)` guarantees rapid open-position resolution.

---

## 3. Concurrency Protection & Lost-Update Elimination

### A. The Flaw of `os.replace()` Alone
In legacy implementations, atomic `os.replace()` was used without file locking:
```
Process A reads ledger (free_cash = 100k)
Process B reads ledger (free_cash = 100k)
Process A deducts 10k and replaces file (free_cash = 90k)
Process B deducts 5k and replaces file (free_cash = 95k)
-> Process A's update is completely LOST.
```

### B. Hardened Transactional File Locking
Phase 3.2 implements `locked_ledger_transaction(ledger_path, update_fn)`:
1. **Acquire Exclusive Lock:** Atomically acquires `ledger.lock` with PID registration.
2. **Read Under Lock:** Reads the freshest committed state from disk.
3. **Backup Before Mutation:** Creates `ledger.backup` snapshot.
4. **Mutate Within Lock:** Invokes `update_fn(ledger_data)`.
5. **Atomic Flush:** Writes to `ledger.tmp_<pid>_<ts>`, flushes OS buffer, and executes `os.replace`.
6. **Release Lock:** Evicts lock file.

Any concurrent process attempting to write waits for lock release, then reads the newly committed state. Lost updates are mathematically eliminated.

---

## 4. Signal & Order Idempotency Invariants

| Guard Trigger | Mechanism | Concurrency Result |
|---|---|---|
| Re-emitted Signal | `UNIQUE(signal_id)` in SQLite | Rejected as duplicate signal |
| In-Flight Order | `status IN ('CREATED', 'VALIDATED', 'SUBMITTED')` | Blocked by `can_admit_order` |
| Uncertain Order | `status = 'REQUIRES_OPERATOR_REVIEW'` | Blocked by `can_admit_order` |
| Active Position | `status = 'OPEN'` in positions table | Blocked by `can_admit_order` |
| Simultaneous Exits | `self.in_flight_exits` memory set | Blocked by `exit_position` |
| Daily Trade Limit | Quota check (`>= 30 trades/day`) | Blocked by `can_admit_order` |

