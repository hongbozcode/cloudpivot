from os import getenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title='CloudPivot')

allowed_origins = [
    origin
    for value in getenv('CLOUDPIVOT_CORS_ORIGINS', '').split(',')
    if (origin := value.strip())
]
if allowed_origins:
    app.add_middleware(CORSMiddleware, allow_origins=allowed_origins)


@app.get('/api/health')
async def health() -> dict[str, str]:
    return {'status': 'ok'}
