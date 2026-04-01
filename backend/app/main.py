# main.py — FastAPI 앱 엔트리포인트
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ad_costs, channels, orders, products, sync

app = FastAPI(title="ohisell API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(channels.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(sync.router)
app.include_router(ad_costs.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
