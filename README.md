# Aurestra Backend

## Exposed APIs

### Authentication
All protected endpoints use:
* **Authentication Type:** JWT (Bearer Token)
* **Header Required:**
  ```
  Authorization: Bearer <JWT_TOKEN>
  Content-Type: application/json
  ```

### Exposed APIs

#### 1. Authentication API
**Endpoint:** `/api/auth/login`
**Method:** POST
**Authentication:** None
**Headers:** Content-Type: application/json

#### 2. Transactions API
**Endpoint:** `/api/transactions`
**Method:** GET
**Authentication:** JWT Bearer Token
**Headers:** Authorization, Content-Type

**Endpoint:** `/api/transactions`
**Method:** POST
**Authentication:** JWT Bearer Token
**Headers:** Authorization, Content-Type

#### 3. Category API
**Endpoint:** `/api/categories/monthly`
**Method:** GET
**Query Parameter:** `month=YYYY-MM`
**Authentication:** JWT Bearer Token
**Headers:** Authorization
Returns aggregated category totals for selected month sorted highest to lowest.

---

## Push notifications (FCM)

The app registers FCM tokens with `POST /api/register-device`. The server sends via Firebase Admin using either:

- **`FIREBASE_CREDENTIALS`** — full service-account JSON as a **single line** in the environment (typical on PaaS). Invalid JSON (quotes/newlines mangled by `.env`) is the most common reason pushes work locally (file path) but not on the server.
- **`FIREBASE_CRED_PATH`** — path to the same JSON file on disk (typical on your laptop).

**Debug (authenticated):** `GET /api/debug/push-status` with `Authorization: Bearer <JWT>`. Returns whether Firebase initialized, `firebase_project_id`, token count, and short **tips** (no private keys).

Server logs use the logger `aurestra.push` and print `[push]` lines for each send attempt. If `device_token_count` is `0`, the phone build is not calling **this** host’s base URL for `/api/register-device`.

## Database migrations and seeders

- **Incremental upgrades:** from `backend/`, run `python3 run_migrations.py` (applies `backend/migrations/*.sql` in order). Re-running is safe for idempotent / “already exists” / duplicate-column cases.
- **Fresh schema + seeds:** `python3 db/setup.py` applies `db/migrations/*.sql` then runs per-table seed files under `db/seeders/` **only for empty tables** (if you dropped data but tables exist with rows, seeders for those tables are skipped).
- **Full wipe + recreate:** `python3 db/setup.py --reset` drops all tables, reapplies schema, then seeds empty tables. You will be prompted to type `yes`.

After dropping the database, run migrations (or `db/setup.py` with `--reset`) so columns such as `statement_account_numbers` / `account_balance_source` exist before relying on statement balance routing.

## E-statement → wallet balance

Closing balance from Gmail PDF import is applied to **one** `account_balances` row:

1. Prefer rows where **`statement_account_numbers`** (JSON array of strings, full or partial account numbers) matches digits detected in the PDF.
2. Otherwise fall back to **`TARGET_ACCOUNT_NUMBER`** in `.env` plus a single bank row, or `source='bank'`, or a single `AccountBalance` row.

The chosen slug is stored on **`statement_analysis.account_balance_source`**. Configure `statement_account_numbers` per wallet when you have multiple banks (SQL `UPDATE` is enough until a UI exists).

---

**Project Version: 1.2**
# Aurestra-backend
