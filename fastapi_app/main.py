"""FastAPI entry point.  Run with:  uvicorn fastapi_app.main:app --port 8001"""
import logging

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from fastapi_app.database import get_db
from fastapi_app.exceptions import register_exception_handlers
from fastapi_app.routers import reconciliation, reports, uploads

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DESCRIPTION = """
Bulk invoice upload, reconciliation against purchase orders, processing
history and reports. Built with **FastAPI + SQLAlchemy + Pydantic**.

Master data (vendors, purchase orders, invoices) is managed by the companion
**Django REST Framework** service; both services share one PostgreSQL
database whose schema is owned by the Django migrations.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Invoice Platform - Processing & Reconciliation API (FastAPI)",
        version="1.0.0",
        description=DESCRIPTION,
    )
    register_exception_handlers(app)
    app.include_router(uploads.router)
    app.include_router(reconciliation.router)
    app.include_router(reports.router)

    @app.get("/health", tags=["Health"], summary="Liveness + database check")
    def health(db: Session = Depends(get_db)):
        db.execute(text("SELECT 1"))
        return {"status": "ok", "service": "fastapi", "database": "ok"}

    return app


app = create_app()
