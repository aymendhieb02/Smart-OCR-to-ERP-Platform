from types import SimpleNamespace
from app.tender_dossier import (
    DocumentRole, Evidence, FactObservation, GroupingStatus, ProcessedTenderDocument,
    RoleStatus, assess_grouping, build_tender_dossier, classify_document_role, compare_facts,
)
def doc(i,name,*,meta=None,facts=()):
    return ProcessedTenderDocument(i,name,SimpleNamespace(id=i),SimpleNamespace(result=i),meta or {},tuple(facts))
def fact(i,value):
    return FactObservation("execution_days",value,Evidence(i,"execution_days",value,text=f"{value} days",page=2,confidence=.9))
def test_single_document_and_processed_output_retained():
    source=doc("d1","CCAP.pdf"); dossier=build_tender_dossier("td1",[source],title="Works",reference="R1")
    assert len(dossier.documents)==1 and dossier.documents[0].document_result is source.document_result
    assert dossier.documents[0].analyzer_output is source.analyzer_output
    assert dossier.documents[0].role.role is DocumentRole.CCAP
def test_multiple_known_and_unknown_roles():
    vals=[classify_document_role(x) for x in ("Avis d'appel d'offres.pdf","CCTP.pdf","BPU.pdf","opaque.bin")]
    assert [x.role for x in vals[:3]]==[DocumentRole.NOTICE,DocumentRole.CCTP,DocumentRole.BPU]
    assert vals[3].role is DocumentRole.UNKNOWN and vals[3].status is RoleStatus.UNKNOWN
    dossier=build_tender_dossier("td", [doc(str(i),n) for i,n in enumerate(("CCAP.pdf","CCTP.pdf","opaque.bin"))])
    assert len(dossier.documents)==3 and any(d.code=="role_review_required" for d in dossier.diagnostics)
def test_conflicts_preserve_evidence_and_equal_values_are_consistent():
    conflict=compare_facts([doc("a","CCAP",facts=[fact("a",90)]),doc("b","CCTP",facts=[fact("b",120)])])
    assert len(conflict)==1 and conflict[0].values==(90,120)
    assert [e.document_id for e in conflict[0].observations]==["a","b"] and conflict[0].observations[1].page==2
    assert compare_facts([doc("a","",facts=[fact("a",90)]),doc("b","",facts=[fact("b",90.0)])])==()
def test_duplicate_documents_reported_but_retained():
    dossier=build_tender_dossier("td",[doc("a","a.pdf",meta={"source_text":"same"}),doc("b","b.pdf",meta={"source_text":"same"})])
    assert len(dossier.documents)==2 and any(d.code=="duplicate_documents" for d in dossier.diagnostics)
def test_explicit_and_identifier_grouping():
    assert assess_grouping({"tender_reference":"AB-12"},{"tender_reference":"ab 12"}).status is GroupingStatus.MATCH
    assert assess_grouping({"explicit_group_id":"G1"},{"explicit_group_id":"G1"}).status is GroupingStatus.MATCH
    assert assess_grouping({"tender_reference":"AB-12"},{"tender_reference":"CD-34"}).status is GroupingStatus.NO_MATCH
    assert assess_grouping({"tender_reference":"AB-12","consultation_number":"C1"},{"tender_reference":"AB-12","consultation_number":"C2"}).status is GroupingStatus.NO_MATCH
def test_ambiguous_grouping_requires_review():
    result=assess_grouping({"organization":"City Works","filename":"tender-annex.pdf"},{"organization":"City Works","filename":"tender.pdf"})
    assert result.status is GroupingStatus.REVIEW
    assert assess_grouping({"filename":"a.pdf"},{"filename":"b.pdf"}).status is GroupingStatus.REVIEW
