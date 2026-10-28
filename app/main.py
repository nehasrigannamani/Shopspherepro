import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from app.api.auth import router as auth_router
from app.api.brands import router as brands_router
from app.api.cart import router as cart_router
from app.api.categories import router as categories_router
from app.api.inventory import router as inventory_router
from app.api.products import router as products_router
from app.core.config import settings
from app.api.product_variants import router as product_variants_router
from app.api.product_media import router as product_media_router
from app.api.product_attributes import router as product_attributes_router
from app.api.product_pricing import router as product_pricing_router
from app.api.variant_inventory import router as variant_inventory_router
from app.api.warehouses import router as warehouses_router
from app.api.inventory_transfers import router as inventory_transfers_router
from app.api.low_stock import router as low_stock_router
from app.api.cart_pricing import router as cart_pricing_router
from app.api.wishlist import router as wishlist_router
from app.api.order_items import router as order_items_router
from app.api.order_status import router as order_status_router
from app.api.coupons import router as coupons_router
from app.api.promotions import router as promotions_router
from app.api.taxes import router as taxes_router
from app.api.shipping import router as shipping_router
from app.api.checkout import router as checkout_router
from app.api.orders import router as orders_router
from app.api.order_cancellation import router as order_cancellation_router
from app.api.payments import router as payments_router
from app.api.payment_transactions import router as payment_transactions_router
from app.api.refunds import router as refunds_router
from app.api.cash_on_delivery import router as cod_router
from app.api.shipping_orders import router as shipping_orders_router
from app.api.shipment_tracking import router as shipment_tracking_router
from app.api.deliveries import router as deliveries_router
from app.api.returns import router as returns_router
from app.api.exchanges import router as exchanges_router
from app.models.product_review import ProductReview
from app.api.product_reviews import router as product_reviews_router
from app.models.product_question import ProductQuestion
from app.api.product_questions import router as product_questions_router
from app.models.support_ticket import SupportTicket
from app.models.support_ticket_message import SupportTicketMessage
from app.api.support_tickets import router as support_tickets_router
from app.models.notification import Notification
from app.api.notifications import router as notifications_router
from app.models.notification_delivery import NotificationDelivery
from app.api.notification_deliveries import (
    router as notification_deliveries_router,
)
from app.models.supplier import Supplier
from app.api.suppliers import router as suppliers_router
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.api.purchase_orders import router as purchase_orders_router
from app.models.supplier_invoice import SupplierInvoice
from app.api.supplier_invoices import router as supplier_invoices_router
from app.models.audit_log import AuditLog
from app.api.audit_logs import router as audit_logs_router
from app.api.dashboard import router as dashboard_router
from app.api.reports import router as reports_router
from app.api.report_exports import router as report_exports_router
from app.api.users import router as users_router
from app.api.profile import router as profile_router
from app.api.addresses import router as addresses_router
from app.core.middleware import AuditMiddleware
from app.services.jobs import scheduler_tick



logger = logging.getLogger("shopsphere")


async def _scheduler_loop():
    while True:
        await asyncio.sleep(settings.scheduler_interval_seconds)
        try:
            await run_in_threadpool(scheduler_tick)
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("Scheduler tick failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = None
    if settings.scheduler_enabled:
        task = asyncio.create_task(_scheduler_loop())
    yield
    if task is not None:
        task.cancel()


app = FastAPI(
    title=settings.app_name,
    description="Backend API for the ShopSphere Pro e-commerce platform.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(AuditMiddleware)


@app.get("/", tags=["Health"])
def root():
    return {
        "message": "Welcome to ShopSphere Pro API",
        "status": "running",
    }


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profile_router)
app.include_router(addresses_router)
app.include_router(products_router)
app.include_router(inventory_router)
app.include_router(cart_router)
app.include_router(categories_router)
app.include_router(brands_router)
app.include_router(product_variants_router)
app.include_router(product_media_router)
app.include_router(product_attributes_router)
app.include_router(product_pricing_router)
app.include_router(variant_inventory_router)
app.include_router(warehouses_router)
app.include_router(inventory_transfers_router)
app.include_router(low_stock_router)
app.include_router(cart_pricing_router)
app.include_router(wishlist_router)
app.include_router(coupons_router)
app.include_router(promotions_router)
app.include_router(taxes_router)
app.include_router(shipping_router)
app.include_router(checkout_router)
app.include_router(orders_router)
app.include_router(order_items_router)
app.include_router(order_status_router)
app.include_router(order_cancellation_router)
app.include_router(payments_router)
app.include_router(payment_transactions_router)
app.include_router(refunds_router)
app.include_router(cod_router)
app.include_router(shipping_orders_router)
app.include_router(shipment_tracking_router)
app.include_router(deliveries_router)


app.include_router(returns_router)
app.include_router(exchanges_router)
app.include_router(product_reviews_router)
app.include_router(product_questions_router)
app.include_router(support_tickets_router)
app.include_router(notifications_router)
app.include_router(notification_deliveries_router)
app.include_router(suppliers_router)
app.include_router(purchase_orders_router)

app.include_router(supplier_invoices_router)
app.include_router(audit_logs_router)
app.include_router(dashboard_router)
app.include_router(reports_router)
app.include_router(report_exports_router)
