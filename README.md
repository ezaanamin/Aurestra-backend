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

**Project Version: 1.1**
# Aurestra-backend
