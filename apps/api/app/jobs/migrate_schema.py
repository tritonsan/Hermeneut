import argparse
import json

from app.services.elastic_service import ElasticService
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare versioned Hermeneut Elastic schema and aliases.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned schema operations without mutating Elastic.")
    parser.add_argument("--apply", action="store_true", help="Apply schema operations.")
    args = parser.parse_args()
    dry_run = not args.apply or args.dry_run
    result = ElasticService(get_settings()).ensure_run_schema(dry_run=dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
