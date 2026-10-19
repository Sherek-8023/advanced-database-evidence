#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
run_dir="artifacts/runs/$run_id"
mkdir -p "$run_dir"
exec > >(tee "$run_dir/pipeline.log") 2>&1
trap 'result=$?; if [ "$result" -ne 0 ]; then echo "FAILED (exit $result). See $run_dir/pipeline.log"; fi' EXIT

echo "Poverty Bank isolated pipeline: $run_id (UTC)"
command -v docker >/dev/null
docker info >/dev/null
docker compose config --quiet

# Include current SQL and application code; unchanged dependencies use Docker's cache.
docker compose build python

docker compose up -d --wait postgres
docker compose run --rm --no-deps python python check.py
docker compose run --rm --no-deps python python run_sql.py
docker compose run --rm --no-deps python python run_validation.py
docker compose run --rm --no-deps python python scripts/verify_pipeline.py

for view in vw_customer_360 vw_transaction_risk_monitor vw_product_opportunity; do
    docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U student -d poverty_bank \
        -c "COPY (SELECT * FROM dw.$view ORDER BY 1, 2, 3) TO STDOUT WITH CSV HEADER" \
        > "$run_dir/$view.csv"
done
docker compose images > "$run_dir/images.txt"
printf '%s\n' "$run_dir" > artifacts/latest-run.txt
echo "SUCCESS: SQL pipeline and profile-specific read-only verification passed."
echo "Logs and three report CSV files: $run_dir"
