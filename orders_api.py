"""
URBANWEAR demo store: orders and products kept on the server.

The store page, the admin page and the chatbot all read the same data from here.
Data is saved in `store_data.json` (created automatically with demo data if missing).

Everything in the product list below comes from docs/04-product-catalog.txt
(names, SKUs, prices in USD, colors, sizes, Final Sale items).
Shipping rules come from docs/02-shipping-policy.txt.

Endpoints:
    GET   /api/config                    : public, shipping rules (price, speed)
    GET   /api/products                  : public, products and stock
    POST  /api/orders                    : public, place an order (the server calculates all prices)
    POST  /api/orders/track              : public, track an order (needs order number AND email)
    GET   /api/admin/orders              : ADMIN_KEY only, all orders
    PATCH /api/admin/orders/{id}         : ADMIN_KEY only, change status
    PATCH /api/admin/products/{id}       : ADMIN_KEY only, change price and stock

Prices are stored in CENTS (integers) so there are no rounding mistakes. 2499 = $24.99.

For the chatbot: order_reply(query) builds an English answer when the question contains
an order number (like ORD-1001) and the email used for the order.
"""

import os
import re
import json
import time
import hmac
import logging
import threading
from collections import defaultdict, deque
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger("orders_api")

DATA_FILE = os.path.join(os.getenv("DATA_DIR", "."), "store_data.json")
DATA_VERSION = 2   # old (Bangladesh demo) files have no version, so they are replaced safely
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
_lock = threading.Lock()

STATUS_EN = [
    "Order received",
    "Packed, waiting for carrier pickup",
    "Shipped",
    "Delivered",
]

# ---------------------------------------------------------
# Shipping rules (docs/02-shipping-policy.txt, section 2)
# ---------------------------------------------------------
SHIPPING = {
    "standard": {"label": "Standard Shipping", "cost_cents": 499, "eta": "3-5 business days"},
    "express": {"label": "Express Shipping", "cost_cents": 1299, "eta": "1-2 business days"},
}
FREE_STANDARD_OVER_CENTS = 7500   # "Free standard shipping on orders over $75"
_PO_BOX = re.compile(r"\bP\.?\s*O\.?\s*Box\b", re.I)   # Express is not available for PO boxes


def shipping_cost(method: str, subtotal_cents: int) -> int:
    if method == "standard" and subtotal_cents > FREE_STANDARD_OVER_CENTS:
        return 0
    return SHIPPING[method]["cost_cents"]


# ---------------------------------------------------------
# Rate limit (per IP, 30 per minute) and admin key check
# ---------------------------------------------------------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 30:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a minute.")
    q.append(now)


def require_admin(request: Request, x_admin_key: str = Header(default="")):
    if not ADMIN_KEY:
        raise HTTPException(status_code=503, detail="ADMIN_KEY is not set on the server, so this action is disabled.")
    if not hmac.compare_digest(x_admin_key.encode("utf-8"), ADMIN_KEY.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Not allowed. Please enter the correct admin key.")


router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit)])


# ---------------------------------------------------------
# Demo data (the product list is copied from docs/04-product-catalog.txt)
# ---------------------------------------------------------
# Stock is NOT in the catalog document. Every product starts with the same
# placeholder number; change it in the admin page.
DEFAULT_STOCK = 25

TOPS = ["S", "M", "L", "XL", "XXL"]
TOPS_XL = ["S", "M", "L", "XL"]
WOMENS = ["XS", "S", "M", "L", "XL"]
PANTS = [f"{w}x{i}" for w in (30, 32, 34, 36) for i in (30, 32, 34)]   # waist x inseam, inches
ONE = ["One size"]

MENS = "Men's and Unisex"
WOMEN = "Women's"
ACC = "Accessories"


def _p(pid, sku, name, cat, price_cents, material, colors, sizes, care, details=None, final_sale=None):
    return {
        "id": pid, "sku": sku, "name": name, "cat": cat, "price_cents": price_cents,
        "material": material, "colors": colors, "sizes": sizes, "care": care,
        "details": details or [], "final_sale": final_sale or [],   # ["Navy"] = that color only, ["*"] = all colors
        "stock": DEFAULT_STOCK,
    }


def _catalog() -> list:
    return [
        _p(1, "UW-101", "Classic Cotton Tee", MENS, 2499, "100% organic cotton",
           ["White", "Black", "Navy", "Grey"], TOPS, "Machine wash cold; tumble dry low.", ["Men's sizing"]),
        _p(2, "UW-102", "Everyday Hoodie", MENS, 5499, "80% cotton, 20% polyester fleece",
           ["Black", "Charcoal", "Forest Green"], TOPS, "Machine wash cold; do not bleach.", ["Men's sizing"]),
        _p(3, "UW-103", "Oversized Hoodie", MENS, 5999, "100% heavyweight cotton",
           ["Cream", "Black", "Dusty Pink"], TOPS_XL, "Machine wash cold; tumble dry low.",
           ["Unisex sizing", "Fit: oversized, loose fit"]),
        _p(4, "UW-201", "Slim Fit Jeans", MENS, 6499, "98% cotton, 2% elastane (stretch denim)",
           ["Dark Blue", "Light Blue", "Black"], PANTS, "Machine wash inside out in cold water.",
           ["Sizes are waist x inseam, in inches"]),
        _p(5, "UW-202", "Relaxed Chino Pants", MENS, 5999, "97% cotton, 3% elastane",
           ["Khaki", "Olive", "Navy"], PANTS, "Machine wash warm; iron on medium if needed.",
           ["Sizes are waist x inseam, in inches"]),
        _p(6, "UW-401", "Waterproof Rain Jacket", MENS, 9999, "Recycled polyester shell with waterproof coating",
           ["Black", "Olive", "Yellow"], TOPS, "Machine wash cold; do not use fabric softener.", ["Unisex sizing"]),
        _p(7, "UW-402", "Puffer Vest", MENS, 8499, "Recycled polyester fill and shell",
           ["Black", "Navy"], TOPS, "Machine wash cold; tumble dry low with dryer balls.",
           ["Unisex sizing"], final_sale=["Navy"]),
        _p(8, "UW-403", "Merino Wool Sweater", MENS, 8999, "100% merino wool",
           ["Grey", "Camel", "Dark Green"], TOPS_XL, "Hand wash cold or dry clean; lay flat to dry.", ["Unisex sizing"]),
        _p(9, "UW-301", "Linen Summer Dress", WOMEN, 7999, "100% linen",
           ["White", "Sand", "Sage Green"], WOMENS, "Machine wash cold; hang dry. Linen wrinkles naturally.",
           ["Women's sizing", "Fit: true to size"]),
        _p(10, "UW-302", "Women's Ribbed Tank", WOMEN, 1999, "95% cotton, 5% elastane",
           ["White", "Black", "Beige"], WOMENS, "Machine wash cold; lay flat to dry.", ["Women's sizing"]),
        _p(11, "UW-303", "Wrap Midi Skirt", WOMEN, 4999, "100% viscose",
           ["Black", "Rust", "Emerald"], WOMENS, "Hand wash cold or dry clean.",
           ["Women's sizing"], final_sale=["Rust"]),
        _p(12, "UW-501", "Canvas Tote Bag", ACC, 2299, "Heavy cotton canvas",
           ["Natural", "Black"], ONE, "Spot clean.", ["One size (15 x 16 in)"]),
        _p(13, "UW-502", "Leather Belt", ACC, 3499, "Genuine leather with brushed metal buckle",
           ["Black", "Brown"], TOPS_XL, "Wipe with a dry cloth."),
        _p(14, "UW-503", "Wool Beanie", ACC, 1899, "100% wool",
           ["Charcoal", "Mustard", "Navy"], ONE, "Hand wash cold."),
        _p(15, "UW-504", "Cotton Crew Socks, 3-Pack", ACC, 1499, "80% cotton, 18% nylon, 2% elastane",
           ["White", "Black", "Grey"], ONE, "Machine wash warm.",
           ["Fits US shoe sizes 7-12", "Each 3-pack contains one color"], final_sale=["*"]),
    ]


def _default_data() -> dict:
    # Two fictional sample orders so the "Track order" page can be tried right away.
    return {
        "version": DATA_VERSION,
        "products": _catalog(),
        "orders": {
            "ORD-1001": {
                "stage": 2, "name": "Alex Morgan", "email": "alex.morgan@example.com",
                "addr": "123 Main St, Austin, TX 78701", "shipping_method": "standard",
                "items": [{"sku": "UW-102", "name": "Everyday Hoodie", "color": "Black", "size": "L",
                           "qty": 1, "price_cents": 5499, "final_sale": False}],
                "subtotal_cents": 5499, "shipping_cents": 499, "total_cents": 5998, "date": "2026-11-20",
            },
            "ORD-1002": {
                "stage": 1, "name": "Taylor Brooks", "email": "taylor.brooks@example.com",
                "addr": "45 Oak Ave, Denver, CO 80202", "shipping_method": "standard",
                "items": [{"sku": "UW-101", "name": "Classic Cotton Tee", "color": "White", "size": "M",
                           "qty": 2, "price_cents": 2499, "final_sale": False}],
                "subtotal_cents": 4998, "shipping_cents": 499, "total_cents": 5497, "date": "2026-11-22",
            },
        },
    }


def _save(data: dict):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)  # the file is never half-written, even if the server stops


def _load() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == DATA_VERSION:
            return data
        # Old demo data (different products, prices in taka): keep a copy, start fresh.
        os.replace(DATA_FILE, DATA_FILE + ".old")
        log.warning("Old store_data.json replaced with the URBANWEAR USA demo data (old copy: %s.old)", DATA_FILE)
    data = _default_data()
    _save(data)
    return data


def _is_final_sale(p: dict, color: str) -> bool:
    fs = p.get("final_sale", [])
    return "*" in fs or color in fs


# ---------------------------------------------------------
# Request shapes (wrong or oversized input is stopped here)
# ---------------------------------------------------------
class OrderItem(BaseModel):
    product_id: int
    size: str = Field(..., min_length=1, max_length=20)
    color: str = Field(..., min_length=1, max_length=40)
    qty: int = Field(..., ge=1, le=20)


class OrderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5, max_length=100)
    addr: str = Field(..., min_length=5, max_length=300)
    shipping_method: str = Field(default="standard", max_length=20)
    items: list[OrderItem] = Field(..., min_length=1, max_length=20)


class TrackRequest(BaseModel):
    order_id: str = Field(..., min_length=3, max_length=20)
    email: str = Field(..., min_length=5, max_length=100)


class StagePatch(BaseModel):
    stage: int = Field(..., ge=0, le=3)


class ProductPatch(BaseModel):
    price_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    stock: int | None = Field(default=None, ge=0, le=1_000_000)


_EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _same_email(a: str, b: str) -> bool:
    return hmac.compare_digest(a.strip().lower().encode("utf-8"), b.strip().lower().encode("utf-8"))


# ---------------------------------------------------------
# Open to everyone
# ---------------------------------------------------------
@router.get("/config")
def get_config():
    return {"shipping": SHIPPING, "free_standard_over_cents": FREE_STANDARD_OVER_CENTS}


@router.get("/products")
def list_products():
    with _lock:
        return {"products": _load()["products"]}


@router.post("/orders")
def create_order(req: OrderCreate):
    if not _EMAIL_OK.match(req.email.strip()):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if req.shipping_method not in SHIPPING:
        raise HTTPException(status_code=400, detail="Please choose Standard or Express shipping.")
    if req.shipping_method == "express" and _PO_BOX.search(req.addr):
        raise HTTPException(status_code=400, detail="Express Shipping is not available for PO boxes. Please choose Standard Shipping.")

    with _lock:
        data = _load()
        prods = {p["id"]: p for p in data["products"]}

        need: dict[int, int] = defaultdict(int)
        for it in req.items:
            p = prods.get(it.product_id)
            if not p:
                raise HTTPException(status_code=400, detail="One of the products could not be found.")
            if it.size not in p["sizes"]:
                raise HTTPException(status_code=400, detail=f"'{p['name']}' is not available in size {it.size}.")
            if it.color not in p["colors"]:
                raise HTTPException(status_code=400, detail=f"'{p['name']}' is not available in {it.color}.")
            need[it.product_id] += it.qty
        for pid, q in need.items():
            if prods[pid]["stock"] < q:
                raise HTTPException(status_code=409, detail=f"Sorry, '{prods[pid]['name']}' does not have enough stock.")

        items, subtotal = [], 0
        for it in req.items:
            p = prods[it.product_id]
            items.append({
                "sku": p["sku"], "name": p["name"], "color": it.color, "size": it.size, "qty": it.qty,
                "price_cents": p["price_cents"], "final_sale": _is_final_sale(p, it.color),
            })
            subtotal += p["price_cents"] * it.qty
        for pid, q in need.items():
            prods[pid]["stock"] -= q

        ship = shipping_cost(req.shipping_method, subtotal)
        nums = [int(k.split("-")[1]) for k in data["orders"]]
        order_id = f"ORD-{max(nums + [1000]) + 1}"
        data["orders"][order_id] = {
            "stage": 0, "name": req.name.strip(), "email": req.email.strip().lower(), "addr": req.addr.strip(),
            "shipping_method": req.shipping_method, "items": items,
            "subtotal_cents": subtotal, "shipping_cents": ship, "total_cents": subtotal + ship,
            "date": date.today().isoformat(),
        }
        _save(data)
    log.info("New order: %s (total %s cents)", order_id, subtotal + ship)
    return {"order_id": order_id, "subtotal_cents": subtotal, "shipping_cents": ship, "total_cents": subtotal + ship}


@router.post("/orders/track")
def track_order(req: TrackRequest):
    # Policy: tracking needs the order number AND the email address.
    # A wrong number and a wrong email give the same answer, so nobody can guess order numbers.
    oid = req.order_id.strip().upper()
    with _lock:
        o = _load()["orders"].get(oid)
    if not o or not _same_email(o["email"], req.email):
        raise HTTPException(status_code=404, detail="We could not find an order with that order number and email.")
    return {
        "order_id": oid, "stage": o["stage"], "status": STATUS_EN[o["stage"]],
        "shipping_method": o["shipping_method"], "eta": SHIPPING[o["shipping_method"]]["eta"],
        "items": [{"name": i["name"], "color": i["color"], "size": i["size"], "qty": i["qty"],
                   "final_sale": i.get("final_sale", False)} for i in o["items"]],
        "date": o["date"],
    }


# ---------------------------------------------------------
# Admin only
# ---------------------------------------------------------
@router.get("/admin/orders", dependencies=[Depends(require_admin)])
def admin_orders():
    with _lock:
        orders = _load()["orders"]
    rows = [{"order_id": k, **v} for k, v in orders.items()]
    rows.sort(key=lambda r: int(r["order_id"].split("-")[1]), reverse=True)
    return {"orders": rows}


@router.patch("/admin/orders/{order_id}", dependencies=[Depends(require_admin)])
def admin_set_stage(order_id: str, req: StagePatch):
    with _lock:
        data = _load()
        o = data["orders"].get(order_id.upper())
        if not o:
            raise HTTPException(status_code=404, detail="No order with this number.")
        o["stage"] = req.stage
        _save(data)
    return {"order_id": order_id.upper(), "stage": req.stage}


@router.patch("/admin/products/{product_id}", dependencies=[Depends(require_admin)])
def admin_edit_product(product_id: int, req: ProductPatch):
    with _lock:
        data = _load()
        p = next((x for x in data["products"] if x["id"] == product_id), None)
        if not p:
            raise HTTPException(status_code=404, detail="Product not found.")
        if req.price_cents is not None:
            p["price_cents"] = req.price_cents
        if req.stock is not None:
            p["stock"] = req.stock
        _save(data)
    return p


# ---------------------------------------------------------
# For the chatbot: if the question has an order number AND the email used for the order,
# look up the real order and answer in English. Otherwise ask for what is missing.
# Returns None when the question is not about an order, so the bot answers from the documents.
# ---------------------------------------------------------
_ORDER_ID = re.compile(r"(?<![A-Za-z0-9])ORD[-\s]?(\d{4,6})(?!\d)", re.I)
_EMAIL_IN_TEXT = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_ORDER_INTENT = re.compile(
    r"(order|parcel|package).*(where|status|track)"
    r"|(where|status|track).*(order|parcel|package)",
    re.I | re.S,
)


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def order_reply(query: str) -> str | None:
    m = _ORDER_ID.search(query)
    if not m:
        if _ORDER_INTENT.search(query):
            return "To check your order, please send your order number (for example ORD-1001) and the email address used for the order."
        return None

    order_id = f"ORD-{m.group(1)}"
    em = _EMAIL_IN_TEXT.search(query)
    if not em:
        return f"To check {order_id}, please also send the email address used for the order."

    with _lock:
        o = _load()["orders"].get(order_id)
    if not o or not _same_email(o["email"], em.group(0).strip(".,;:!?")):
        return "Sorry, I could not find an order with that order number and email. Please check both and try again."

    items = ", ".join(f"{i['name']} ({i['color']}, {i['size']}) x{i['qty']}" for i in o["items"])
    ship = SHIPPING[o["shipping_method"]]
    return (f"Order {order_id}: {STATUS_EN[o['stage']]}.\n"
            f"Items: {items}\n"
            f"Shipping: {ship['label']} ({ship['eta']})\n"
            f"Total: {_money(o['total_cents'])}")
