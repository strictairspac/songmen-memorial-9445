from starlette.responses import Response

from src.config.settings import engine
from src.models.tables import evidence, supplement_orders
from src.services.workflow import found
from src.utils.encoding import canonical


def download(identity):
    with engine.connect() as conn:
        data = found(conn, evidence, identity)
    return Response(canonical(data), media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="evidence-{identity}.json"'})


def download_supplement(identity):
    with engine.connect() as conn:
        data = found(conn, supplement_orders, identity)
    return Response(canonical(data), media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="supplement-{identity}.json"'})
