from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from src.config.settings import cache, engine
from src.controllers.workflow import download, download_supplement
from src.models.tables import audit, cases, evidence, runs, supplement_orders
from src.schemas.workflow import (CaseForm, CorrectionForm, LoginForm,
                                  ReviewForm, SupplementReviewForm)
from src.services import domain, workflow
from src.services.security import current_user, login, operator, reviewer

router = APIRouter()


@router.get('/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1')).scalar_one()
    cache.ping()
    return {'ok': True}


@router.post('/login')
def sign_in(form: LoginForm):
    return login(form.username, form.password)


@router.get('/spec')
def spec(_=Depends(current_user)):
    return domain.SPEC


@router.get('/cases')
def list_cases(_=Depends(current_user)):
    return workflow.recent(cases)


@router.post('/cases', status_code=201)
def create_case(form: CaseForm, actor=Depends(operator)):
    return workflow.make_case(form.title, form.payload, actor)


@router.get('/runs')
def list_runs(_=Depends(current_user)):
    return workflow.recent(runs)


@router.post('/cases/{case_id}/runs', status_code=202)
def start_run(case_id: int, actor=Depends(operator)):
    return workflow.enqueue(case_id, actor)


@router.get('/evidence')
def list_evidence(_=Depends(current_user)):
    return workflow.recent(evidence)


@router.get('/evidence/{identity}/download')
def export_evidence(identity: int, _=Depends(current_user)):
    return download(identity)


@router.post('/evidence/{identity}/review')
def review_evidence(identity: int, form: ReviewForm, actor=Depends(reviewer)):
    return workflow.review(identity, form.note, actor)


@router.get('/supplements')
def list_supplements(_=Depends(current_user)):
    return workflow.recent(supplement_orders)


@router.get('/cases/{case_id}/adoption')
def case_adoption(case_id: int, _=Depends(current_user)):
    return workflow.month_adoption(case_id)


@router.post('/cases/{case_id}/supplements')
def create_supplement(case_id: int, form: CorrectionForm, response: Response, actor=Depends(operator)):
    order, created = workflow.create_supplement(
        case_id, form.basis_evidence_id, form.month, form.corrected_state, actor)
    response.status_code = 201 if created else 200
    return {'created': created, 'order': order}


@router.post('/supplements/{identity}/review')
def review_supplement(identity: int, form: SupplementReviewForm, actor=Depends(reviewer)):
    return workflow.review_supplement(identity, form.note, actor)


@router.get('/supplements/{identity}/download')
def export_supplement(identity: int, _=Depends(current_user)):
    return download_supplement(identity)


@router.get('/audit')
def list_audit(_=Depends(current_user)):
    return workflow.recent(audit)
