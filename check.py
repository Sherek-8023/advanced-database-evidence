import psycopg2

conn = psycopg2.connect(
    dbname="poverty_bank",
    user="student",
    password="student",
    host="postgres",
    port=5432
)

cur = conn.cursor()
cur.execute("SELECT current_database();")
print(cur.fetchone())

cur.close()
conn.close()