from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
try:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    print("SUCCESS")
except Exception as e:
    print("FAILED:", str(e))
