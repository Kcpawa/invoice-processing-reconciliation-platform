"""python manage.py seed_demo_data

Creates the vendors and purchase orders that samples/sample_invoices.csv
refers to. Safe to run more than once.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.purchase_orders.models import PurchaseOrder
from apps.vendors.models import Vendor

VENDORS = [
    # code, name, email, tax id, status
    ("V001", "Acme Supplies Pvt Ltd", "ap@acme-supplies.example", "29ABCDE1234F1Z5", "ACTIVE"),
    ("V002", "Globex Traders", "billing@globex.example", "27FGHIJ5678K1Z2", "ACTIVE"),
    ("V003", "Initech Services", "accounts@initech.example", "07KLMNO9012P1Z8", "INACTIVE"),
    ("V004", "Umbrella Logistics", "finance@umbrella.example", "33QRSTU3456V1Z1", "ACTIVE"),
]

PURCHASE_ORDERS = [
    # number, vendor, description, order date, amount, tax, status
    ("PO-1001", "V001", "Office laptops", date(2026, 1, 5), "50000.00", "9000.00", "OPEN"),
    ("PO-1002", "V001", "Printer consumables", date(2026, 1, 12), "2000.00", "360.00", "OPEN"),
    ("PO-2001", "V002", "Warehouse racking", date(2026, 1, 20), "20000.00", "3600.00", "OPEN"),
    ("PO-3001", "V003", "Annual maintenance", date(2026, 1, 25), "10000.00", "1800.00", "OPEN"),
    ("PO-4001", "V004", "Freight - cancelled", date(2026, 2, 1), "7500.00", "1350.00", "CANCELLED"),
]


class Command(BaseCommand):
    help = "Create demo vendors and purchase orders matching the sample upload files."

    @transaction.atomic
    def handle(self, *args, **options):
        vendors = {}
        for code, name, email, tax_id, status in VENDORS:
            vendors[code], created = Vendor.objects.get_or_create(
                vendor_code=code,
                defaults={"name": name, "email": email, "tax_id": tax_id, "status": status},
            )
            self.stdout.write(f"{'created' if created else 'exists '}  vendor {code}  {name}")

        for number, vendor_code, description, order_date, amount, tax, status in PURCHASE_ORDERS:
            total = Decimal(amount) + Decimal(tax)
            _, created = PurchaseOrder.objects.get_or_create(
                po_number=number,
                defaults={
                    "vendor": vendors[vendor_code],
                    "description": description,
                    "order_date": order_date,
                    "amount": Decimal(amount),
                    "tax_amount": Decimal(tax),
                    "total_amount": total,
                    "remaining_balance": total,
                    "status": status,
                },
            )
            self.stdout.write(f"{'created' if created else 'exists '}  PO {number}  total {total}")

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
