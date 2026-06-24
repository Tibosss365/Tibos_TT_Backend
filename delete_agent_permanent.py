"""
Permanently delete agent(s) by username — for agents that won't go away via the
UI because the running backend hasn't been redeployed with the fixed delete
endpoint yet.

It does exactly what the fixed DELETE /agents/{id} endpoint does: detaches every
foreign-key reference to the user (nullable columns -> NULL, non-nullable -> the
rows are deleted) and then removes the user row.

Uses the backend's own DATABASE_URL (.env), so it hits the same database the app
uses. Runs inside a single transaction — all-or-nothing.

USAGE (from the backend folder, with the venv active):
    python delete_agent_permanent.py kaif@tibos.in siva@tibos.in
"""
import asyncio
import sys

from sqlalchemy import text

from app.database import engine

FK_Q = """
SELECT tc.table_name, kcu.column_name, col.is_nullable
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
  ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
JOIN information_schema.columns col
  ON col.table_schema = tc.table_schema AND col.table_name = tc.table_name AND col.column_name = kcu.column_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND ccu.table_name = 'users' AND ccu.column_name = 'id'
"""


async def main(usernames):
    async with engine.begin() as c:  # one transaction; rolls back on any error
        refs = (await c.execute(text(FK_Q))).fetchall()
        for username in usernames:
            row = (await c.execute(
                text("SELECT id, name, role FROM users WHERE lower(username) = lower(:u)"),
                {"u": username},
            )).first()
            if not row:
                print(f"!! no user with username '{username}' — skipped")
                continue
            uid = str(row.id)
            print(f"\nDeleting {row.name} ({username}, role={row.role}) ...")
            for table_name, column_name, is_nullable in refs:
                if is_nullable == "YES":
                    res = await c.execute(
                        text(f'UPDATE "{table_name}" SET "{column_name}" = NULL WHERE "{column_name}" = CAST(:uid AS uuid)'),
                        {"uid": uid},
                    )
                    if res.rowcount:
                        print(f"   NULLed {res.rowcount:>3} in {table_name}.{column_name}")
                else:
                    res = await c.execute(
                        text(f'DELETE FROM "{table_name}" WHERE "{column_name}" = CAST(:uid AS uuid)'),
                        {"uid": uid},
                    )
                    if res.rowcount:
                        print(f"   deleted {res.rowcount:>3} in {table_name}.{column_name}")
            await c.execute(text("DELETE FROM users WHERE id = CAST(:uid AS uuid)"), {"uid": uid})
            print(f"   -> user row removed")
    print("\nDone.")


if __name__ == "__main__":
    # Default to the two agents that keep reappearing; override by passing
    # usernames as arguments.
    args = sys.argv[1:] or ["kaif@tibos.in", "siva@tibos.in"]
    asyncio.run(main(args))
