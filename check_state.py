import store

con = store.connect()
rows = con.execute("SELECT id, status, started_at FROM quiz WHERE status IN ('ready', 'active')").fetchall()
print("идущих квизов:", len(rows), [dict(r) for r in rows])
