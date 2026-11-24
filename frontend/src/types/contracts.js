/** @typedef {{id:number, title:string, payload:object, author:string}} BusinessCase */
/** @typedef {{id:number, case_id:number, status:'queued'|'running'|'complete'|'failed'}} Run */
/** @typedef {{id:number, run_id:number, digest:string, report:{summary:string, metrics:object, sections:Array}}} Evidence */
/** @typedef {{id:number, case_id:number, basis_evidence_id:number, month:string, corrected_state:string, digest:string, status:'effective'|'superseded', reviewer:?string, review_note:?string, reviewed_at:?string, report:object}} SupplementOrder */
