# IT Helpdesk Backend

FastAPI + PostgreSQL + Redis backend for the IT support ticketing system.

## Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI (async) |
| ORM | SQLAlchemy 2.x (async) + asyncpg |
| Database | PostgreSQL |
| Cache | Redis |
| Auth | JWT (python-jose + passlib/bcrypt) |
| Real-time | SSE (notifications) + WebSocket (internal comms) |
| Migrations | Alembic |

---

## Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Redis 7+

---

## Setup

### 1. Create a virtual environment

```bash
cd backend
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your PostgreSQL and Redis connection strings
```

### 4. Create the database

```sql
-- In psql:
CREATE DATABASE helpdesk;
```

### 5. Run migrations

```bash
alembic upgrade head
```

### 6. Seed initial data

```bash
python seed.py
```

This creates 5 default agents and 15 sample tickets:

| Username | Password | Role |
|----------|----------|------|
| admin | admin | admin |
| sarah | sarah123 | technician |
| marcus | marcus123 | technician |
| priya | priya123 | technician |
| tom | tom123 | technician |

### 7. Start the server

```bash
uvicorn main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

---

## API Reference

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/login` | Login → JWT token |
| GET | `/auth/me` | Current user info |
| POST | `/auth/logout` | Logout (client discards token) |

### Tickets
| Method | Path | Description |
|--------|------|-------------|
| GET | `/tickets` | List tickets (filter, sort, paginate) |
| POST | `/tickets` | Create ticket |
| GET | `/tickets/mine` | My assigned tickets |
| GET | `/tickets/export` | Export as CSV |
| GET | `/tickets/{id}` | Get ticket detail |
| PATCH | `/tickets/{id}` | Update ticket |
| DELETE | `/tickets/{id}` | Delete ticket |
| POST | `/tickets/{id}/comments` | Add comment |
| POST | `/tickets/bulk` | Bulk resolve/close/delete |

### Agents
| Method | Path | Description |
|--------|------|-------------|
| GET | `/agents` | List all agents |
| POST | `/agents` | Create agent (admin) |
| GET | `/agents/{id}` | Get agent |
| PATCH | `/agents/{id}` | Update agent (admin) |
| DELETE | `/agents/{id}` | Delete agent (admin) |

### Notifications
| Method | Path | Description |
|--------|------|-------------|
| GET | `/notifications` | Get my notifications |
| PATCH | `/notifications/{id}/read` | Mark one read |
| PATCH | `/notifications/read-all` | Mark all read |
| DELETE | `/notifications` | Clear all |

### Admin
| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/stats` | Dashboard/admin stats |
| GET | `/admin/sla` | Get SLA config |
| PUT | `/admin/sla` | Update SLA config |
| GET | `/admin/email` | Get email config |
| PUT | `/admin/email` | Update email config |

### Analytics
| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics` | Full analytics payload |

### Real-time
| Protocol | Path | Description |
|----------|------|-------------|
| GET (SSE) | `/events?token=<jwt>` | Server-Sent Events stream |
| WebSocket | `/ws?token=<jwt>` | Bidirectional internal comms |

---

## Real-time Events

### SSE Events (subscribe at `/events?token=<jwt>`)

| Event | Payload | When |
|-------|---------|------|
| `connected` | `{user_id}` | On connect |
| `notification` | Notification object | New notification |
| `ticket_created` | `{ticket_id, ticket_number}` | Ticket created |
| `ticket_updated` | `{ticket_id, ticket_number}` | Ticket updated |
| `ticket_deleted` | `{ticket_id}` | Ticket deleted |
| `ticket_comment` | `{ticket_id, author}` | Comment added |
| `tickets_bulk_updated` | `{action, count}` | Bulk operation |

### WebSocket Messages (connect at `/ws?token=<jwt>`)

**Send from client:**
```json
{"type": "ping"}
{"type": "message", "text": "Hello", "to": "<user_id>"}
{"type": "typing"}
```

**Receive from server:**
```json
{"type": "pong"}
{"type": "connected", "user_id": "...", "name": "..."}
{"type": "user_online", "user_id": "...", "name": "..."}
{"type": "user_offline", "user_id": "...", "name": "..."}
{"type": "message", "from_user_id": "...", "from_name": "...", "text": "..."}
{"type": "typing", "user_id": "...", "name": "..."}
{"type": "notification", ...notification fields}
{"type": "ticket_created"|"ticket_updated"|..., ...fields}
```

---

## Redis Cache Strategy

| Key Pattern | TTL | Invalidated on |
|-------------|-----|----------------|
| `tickets:list:*` | 30s | Any ticket write |
| `stats:dashboard` | 60s | Any ticket write |
| `analytics:main` | 120s | Any ticket write |

---

## Project Structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app + CORS + lifespan
│   ├── config.py            # Pydantic settings
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── redis_client.py      # Async Redis client
│   ├── models/              # SQLAlchemy ORM models
│   │   ├── user.py
│   │   ├── ticket.py
│   │   ├── notification.py
│   │   └── admin.py
│   ├── schemas/             # Pydantic request/response schemas
│   │   ├── user.py
│   │   ├── ticket.py
│   │   ├── notification.py
│   │   ├── admin.py
│   │   └── analytics.py
│   ├── routers/             # FastAPI route handlers
│   │   ├── auth.py
│   │   ├── agents.py
│   │   ├── tickets.py
│   │   ├── notifications.py
│   │   ├── admin.py
│   │   ├── analytics.py
│   │   ├── events.py        # SSE endpoint
│   │   └── ws.py            # WebSocket endpoint
│   ├── services/            # Business logic / managers
│   │   ├── sse_manager.py
│   │   ├── ws_manager.py
│   │   ├── cache_service.py
│   │   └── notification_service.py
│   └── core/
│       ├── security.py      # JWT + bcrypt
│       └── deps.py          # FastAPI dependencies
├── alembic/                 # Database migrations
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── alembic.ini
├── seed.py                  # Initial data seeder
├── requirements.txt
└── .env.example
```
