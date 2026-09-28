"""Deterministic multi-document tender dossier aggregation (no OCR or model calls)."""
from __future__ import annotations
import hashlib, re, unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable, Mapping

class DocumentRole(str, Enum):
    NOTICE="NOTICE"; CDC="CDC"; DAO="DAO"; CCAP="CCAP"; CCTP="CCTP"; REGLEMENT="REGLEMENT"
    BOQ="BOQ"; BPU="BPU"; DQE="DQE"; DEVIS="DEVIS"; SOUMISSION="SOUMISSION"; ANNEX="ANNEX"; OTHER="OTHER"; UNKNOWN="UNKNOWN"
class RoleStatus(str, Enum):
    ASSIGNED="assigned"; REVIEW="review"; UNKNOWN="unknown"
class GroupingStatus(str, Enum):
    MATCH="match"; REVIEW="review"; NO_MATCH="no_match"

@dataclass(frozen=True)
class RoleAssessment:
    role: DocumentRole
    confidence: float
    status: RoleStatus
    candidates: tuple[tuple[DocumentRole,float],...]=()
    reasons: tuple[str,...]=()
@dataclass(frozen=True)
class GroupingAssessment:
    status: GroupingStatus
    signal: str|None
    reason: str
@dataclass(frozen=True)
class Evidence:
    document_id: str
    field: str
    value: Any
    text: str|None=None
    page: int|None=None
    bbox: Any=None
    confidence: float|None=None
@dataclass(frozen=True)
class FactObservation:
    key: str
    value: Any
    evidence: Evidence
@dataclass(frozen=True)
class ProcessedTenderDocument:
    """A document already passed through DocumentProcessor and its analyzer."""
    document_id: str
    filename: str
    document_result: Any
    analyzer_output: Any=None
    metadata: Mapping[str,Any]=field(default_factory=dict)
    facts: tuple[FactObservation,...]=()
    text_for_classification: str=""
@dataclass(frozen=True)
class TenderDossierDocument:
    document_id: str
    filename: str
    role: RoleAssessment
    source_metadata: Mapping[str,Any]
    analyzer_output: Any=None
    document_result: Any=None
    content_digest: str|None=None
@dataclass(frozen=True)
class PotentialConflict:
    fact_key: str
    observations: tuple[Evidence,...]
    values: tuple[Any,...]
    message: str="Documents report different values; review cited evidence."
@dataclass(frozen=True)
class DossierDiagnostic:
    code: str
    message: str
    document_ids: tuple[str,...]=()
@dataclass
class TenderDossier:
    dossier_id: str
    title: str|None=None
    reference: str|None=None
    documents: list[TenderDossierDocument]=field(default_factory=list)
    relationships: list[Mapping[str,Any]]=field(default_factory=list)
    shared_metadata: dict[str,Any]=field(default_factory=dict)
    conflicts: list[PotentialConflict]=field(default_factory=list)
    diagnostics: list[DossierDiagnostic]=field(default_factory=list)

def _norm(value:object)->str:
    text=unicodedata.normalize("NFKD",str(value).casefold())
    text="".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+"," ",text).strip()

_RULES=(
 (DocumentRole.NOTICE,("avis d appel d offres","appel d offres","avis de marche")),
 (DocumentRole.CCAP,("ccap","cahier des clauses administratives particulieres")),
 (DocumentRole.CCTP,("cctp","cahier des clauses techniques particulieres")),
 (DocumentRole.CDC,("cahier des charges","cdc")),
 (DocumentRole.DAO,("dao","dossier d appel d offres")),
 (DocumentRole.REGLEMENT,("reglement de consultation","reglement de la consultation")),
 (DocumentRole.BOQ,("bill of quantities","boq")),
 (DocumentRole.BPU,("bordereau des prix unitaires","bpu")),
 (DocumentRole.DQE,("detail quantitatif estimatif","devis quantitatif estimatif","dqe")),
 (DocumentRole.DEVIS,("devis","quotation")),
 (DocumentRole.SOUMISSION,("acte de soumission","lettre de soumission","soumission")),
 (DocumentRole.ANNEX,("annexe","annex","appendix")),
)
def classify_document_role(filename:str,text:str="")->RoleAssessment:
    """Classify only explicit anchors; retain unknown/review states."""
    normalized=_norm(filename+" "+text); found={}
    for role,anchors in _RULES:
        for anchor in anchors:
            needle=_norm(anchor)
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",normalized):
                found[role]=.98 if len(needle.replace(" ",""))>=4 else .92
    ranked=tuple(sorted(found.items(),key=lambda x:(-x[1],x[0].value)))
    if not ranked: return RoleAssessment(DocumentRole.UNKNOWN,0.,RoleStatus.UNKNOWN,reasons=("No reliable role anchor found.",))
    if len(ranked)>1: return RoleAssessment(DocumentRole.UNKNOWN,ranked[0][1],RoleStatus.REVIEW,ranked,("Multiple role anchors require review.",))
    role,score=ranked[0]
    return RoleAssessment(role,score,RoleStatus.ASSIGNED,ranked,(f"Explicit role anchor matched: {role.value}",))

def assess_grouping(left:Mapping[str,object],right:Mapping[str,object])->GroupingAssessment:
    """Exact identifiers may match; weak signals remain review candidates."""
    a,b=left.get("explicit_group_id"),right.get("explicit_group_id")
    if a and b:
        same=_norm(a)==_norm(b)
        return GroupingAssessment(GroupingStatus.MATCH if same else GroupingStatus.NO_MATCH,"explicit_group_id","Explicit dossier ids agree." if same else "Explicit dossier ids differ.")
    comparable=[]
    for key in ("tender_reference","consultation_number"):
        a,b=left.get(key),right.get(key)
        if a and b: comparable.append((key,_norm(a)==_norm(b)))
    if comparable:
        mismatch=next((key for key,same in comparable if not same),None)
        if mismatch:
            return GroupingAssessment(GroupingStatus.NO_MATCH,mismatch,"Available identifiers differ.")
        key=comparable[0][0]
        return GroupingAssessment(GroupingStatus.MATCH,key,"Exact normalized identifiers agree.")
    ol,orr=left.get("organization"),right.get("organization"); fl,fr=left.get("filename"),right.get("filename")
    if ol and orr and fl and fr:
        sl=_norm(str(fl).rsplit(".",1)[0]); sr=_norm(str(fr).rsplit(".",1)[0])
        if _norm(ol)==_norm(orr) and sl and sr and (sl in sr or sr in sl):
            return GroupingAssessment(GroupingStatus.REVIEW,"organization_and_filename","Organization and related filenames suggest a dossier; confirm.")
    return GroupingAssessment(GroupingStatus.REVIEW,None,"Insufficient shared identifiers; require review.")

def _value_key(value:object)->tuple[str,str]:
    if isinstance(value,(int,float,Decimal)):
        try: return "number",format(Decimal(str(value)).normalize(),"f")
        except InvalidOperation: pass
    return "text",_norm(value)

def compare_facts(documents:Iterable[ProcessedTenderDocument])->tuple[PotentialConflict,...]:
    grouped=defaultdict(list)
    for doc in documents:
        for fact in doc.facts: grouped[fact.key].append(fact)
    result=[]
    for key,items in sorted(grouped.items()):
        if len({_value_key(item.value) for item in items})>1:
            result.append(PotentialConflict(key,tuple(x.evidence for x in items),tuple(x.value for x in items)))
    return tuple(result)

def find_duplicates(documents:Iterable[ProcessedTenderDocument])->tuple[tuple[str,...],...]:
    grouped=defaultdict(list)
    for doc in documents:
        digest=doc.metadata.get("content_digest") or doc.metadata.get("sha256")
        source=doc.metadata.get("source_bytes",doc.metadata.get("source_text"))
        if not digest and source is not None:
            if isinstance(source,str): source=source.encode("utf-8")
            if isinstance(source,bytes): digest=hashlib.sha256(source).hexdigest()
        if digest: grouped[str(digest)].append(doc.document_id)
    return tuple(tuple(ids) for ids in grouped.values() if len(ids)>1)

def build_tender_dossier(dossier_id:str,documents:Iterable[ProcessedTenderDocument],*,title:str|None=None,reference:str|None=None,shared_metadata:dict[str,Any]|None=None)->TenderDossier:
    """Attach processed results and analyzer outputs; never processes documents."""
    docs=tuple(documents); attached=[]; diagnostics=[]
    for item in docs:
        role=classify_document_role(item.filename,item.text_for_classification)
        attached.append(TenderDossierDocument(item.document_id,item.filename,role,dict(item.metadata),item.analyzer_output,item.document_result,str(item.metadata.get("content_digest") or item.metadata.get("sha256") or "") or None))
    for ids in find_duplicates(docs): diagnostics.append(DossierDiagnostic("duplicate_documents","Matching content digest; both documents retained.",ids))
    for item in attached:
        if item.role.status is not RoleStatus.ASSIGNED: diagnostics.append(DossierDiagnostic("role_review_required",f"Role status is {item.role.status.value}; role was not forced.",(item.document_id,)))
    return TenderDossier(dossier_id,title,reference,attached,[],dict(shared_metadata or {}),list(compare_facts(docs)),diagnostics)
