# RelayPay

RelayPay is an evidence-first, INR-only payment-orchestration sandbox. It demonstrates one
provider effect and one local financial transition under retries, races, crashes, and ambiguous
provider responses. It accepts synthetic test data only and is not a production payment processor.

The implementation contract lives in [`docs/vibe-coding`](docs/vibe-coding/README.md).

## Development status

Implementation is proceeding through the frozen six-week gates. Backend correctness gates are
completed before the forensic console is enabled.

## Toolchain

- Python 3.12, FastAPI 0.139, Pydantic v2, SQLAlchemy 2.0, Psycopg 3, Alembic
- PostgreSQL and Redis through Docker Compose
- Next.js App Router and TypeScript after the backend go/no-go gate

Copy `.env.example` to `.env`, replace all development secrets, then use the `Makefile` commands.

