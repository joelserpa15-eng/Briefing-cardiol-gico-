#!/usr/bin/env python3
"""
Briefing Cardiológico Semanal — Article Fetcher
================================================
Queries PubMed (primary) and Europe PMC (secondary) for recent high-evidence
cardiology articles from the world's top cardiology journals, ranked by
evidence level and journal impact factor.

Usage:
    python scripts/fetch_articles.py [--days N]

Environment variables:
    NCBI_EMAIL      Required by NCBI ToS (defaults to placeholder)
    NCBI_API_KEY    Optional — raises rate limit from 3 to 10 req/s
"""

import json
import time
import re
import os
import sys
import argparse
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

# ────────────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────────────

NCBI_EMAIL   = os.environ.get("NCBI_EMAIL", "briefing.cardiologico@noreply.com")
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")

ESEARCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EPMC_URL     = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Rate limiting delay (seconds between NCBI calls)
NCBI_DELAY   = 0.12 if NCBI_API_KEY else 0.36

# ────────────────────────────────────────────────────────────────────────────
# Top cardiology journals — ordered by impact factor (approximate 2024/25)
# Each entry: NLM abbreviation used by PubMed | display name | impact factor | rank
# ────────────────────────────────────────────────────────────────────────────
JOURNALS = [
    # General high-impact (routinely publish landmark cardiology trials)
    {"nlm": "N Engl J Med",               "display": "New England Journal of Medicine",              "if": 176, "rank": 1},
    {"nlm": "Lancet",                      "display": "The Lancet",                                   "if": 168, "rank": 2},
    {"nlm": "JAMA",                        "display": "JAMA",                                         "if": 120, "rank": 3},
    {"nlm": "Nat Med",                     "display": "Nature Medicine",                              "if": 82,  "rank": 4},
    # Dedicated cardiology — top tier
    {"nlm": "Eur Heart J",                "display": "European Heart Journal",                       "if": 39,  "rank": 5},
    {"nlm": "Circulation",                "display": "Circulation",                                  "if": 37,  "rank": 6},
    {"nlm": "JAMA Cardiol",               "display": "JAMA Cardiology",                              "if": 24,  "rank": 7},
    {"nlm": "J Am Coll Cardiol",          "display": "Journal of the American College of Cardiology","if": 21,  "rank": 8},
    # Specialty journals — high impact
    {"nlm": "Eur J Heart Fail",           "display": "European Journal of Heart Failure",            "if": 18,  "rank": 9},
    {"nlm": "JACC Heart Fail",            "display": "JACC: Heart Failure",                          "if": 14,  "rank": 10},
    {"nlm": "JACC Cardiovasc Interv",     "display": "JACC: Cardiovascular Interventions",           "if": 11,  "rank": 11},
    {"nlm": "Cardiovasc Res",             "display": "Cardiovascular Research",                      "if": 10,  "rank": 12},
    {"nlm": "JACC Clin Electrophysiol",   "display": "JACC: Clinical Electrophysiology",             "if": 9,   "rank": 13},
    {"nlm": "Arterioscler Thromb Vasc Biol","display":"Arteriosclerosis, Thrombosis, and Vascular Biology","if":9,"rank":14},
    {"nlm": "J Heart Lung Transplant",    "display": "Journal of Heart and Lung Transplantation",    "if": 8,   "rank": 15},
    {"nlm": "Hypertension",               "display": "Hypertension",                                 "if": 7,   "rank": 16},
    {"nlm": "Heart",                      "display": "Heart",                                        "if": 7,   "rank": 17},
    {"nlm": "Heart Rhythm",               "display": "Heart Rhythm",                                 "if": 6,   "rank": 18},
    {"nlm": "Europace",                   "display": "Europace",                                     "if": 6,   "rank": 19},
    {"nlm": "Int J Cardiol",              "display": "International Journal of Cardiology",           "if": 4,   "rank": 20},
    # Additional high-relevance subspecialty journals
    {"nlm": "Circ Arrhythm Electrophysiol","display":"Circulation: Arrhythmia and Electrophysiology","if": 7,  "rank": 18},
    {"nlm": "Circ Cardiovasc Interv",     "display": "Circulation: Cardiovascular Interventions",    "if": 7,   "rank": 18},
    {"nlm": "Circ Heart Fail",            "display": "Circulation: Heart Failure",                   "if": 8,   "rank": 15},
    {"nlm": "Circ Cardiovasc Imaging",    "display": "Circulation: Cardiovascular Imaging",          "if": 7,   "rank": 17},
    {"nlm": "EuroIntervention",           "display": "EuroIntervention",                             "if": 7,   "rank": 17},
    {"nlm": "JACC Cardiovasc Imaging",    "display": "JACC: Cardiovascular Imaging",                 "if": 10,  "rank": 12},
]

NLM_TO_RANK    = {j["nlm"]: j["rank"] for j in JOURNALS}
NLM_TO_DISPLAY = {j["nlm"]: j["display"] for j in JOURNALS}

# ────────────────────────────────────────────────────────────────────────────
# Evidence level mapping  (PubMed publication type → rank)
# Lower rank number = higher evidence
# ────────────────────────────────────────────────────────────────────────────
EVIDENCE_LEVELS = [
    (1, "Meta-análisis",                  ["meta-analysis"]),
    (2, "Revisión Sistemática",           ["systematic review"]),
    (3, "Ensayo Clínico Aleatorizado",    ["randomized controlled trial", "controlled clinical trial",
                                           "clinical trial, phase iii", "clinical trial, phase iv"]),
    (3, "Ensayo Clínico",                 ["clinical trial", "clinical trial, phase ii",
                                           "clinical trial, phase i"]),
    (4, "Estudio Multicéntrico / Cohorte",["multicenter study", "observational study",
                                           "prospective study"]),
    (5, "Estudio Caso-Control",           ["case-control study"]),
    (6, "Serie de Casos",                 ["case reports"]),
    (7, "Artículo Original",              ["journal article", "review"]),
]

def get_evidence(pub_types):
    lowered = [pt.lower() for pt in pub_types]
    for rank, label, keys in EVIDENCE_LEVELS:
        if any(k in pt for k in keys for pt in lowered):
            return rank, label
    return 7, "Artículo Original"

# ────────────────────────────────────────────────────────────────────────────
# Subspecialty definitions
# Each keyword match in the title scores 2 pts; in abstract 1 pt.
# Each MeSH match scores 3 pts. Highest score wins.
# ────────────────────────────────────────────────────────────────────────────
SUBSPECIALTIES = [
    {
        "id": "intervencionista",
        "name": "Cardiología Intervencionista",
        "color": "#C0392B",
        "keywords": [
            "percutaneous coronary intervention", "pci", "coronary stent",
            "drug-eluting stent", "bioresorbable scaffold", "coronary angioplasty",
            "fractional flow reserve", "ffr", "instantaneous wave-free ratio", "ifr",
            "ivus", "intravascular ultrasound", "optical coherence tomography",
            "transcatheter aortic valve", "tavr", "tavi", "valve-in-valve",
            "coronary revascularization", "balloon dilatation", "rotablation",
            "coronary physiology", "bifurcation lesion", "left main",
        ],
        "mesh": [
            "Percutaneous Coronary Intervention", "Stents", "Angioplasty, Balloon, Coronary",
            "Transcatheter Aortic Valve Replacement", "Cardiac Catheterization",
            "Fractional Flow Reserve, Myocardial",
        ],
    },
    {
        "id": "arritmias",
        "name": "Arritmias y Electrofisiología",
        "color": "#7D3C98",
        "keywords": [
            "atrial fibrillation", "atrial flutter", "ventricular tachycardia",
            "ventricular fibrillation", "supraventricular tachycardia", "svt",
            "catheter ablation", "pulmonary vein isolation", "pulsed field ablation",
            "cryoablation", "radiofrequency ablation", "cardiac resynchronization",
            "crt", "implantable cardioverter", "icd", "subcutaneous icd", "s-icd",
            "leadless pacemaker", "pacemaker", "heart block", "sudden cardiac death",
            "wolff-parkinson-white", "long qt", "brugada", "electrophysiology",
            "left bundle branch", "conduction system pacing",
        ],
        "mesh": [
            "Atrial Fibrillation", "Arrhythmias, Cardiac", "Catheter Ablation",
            "Defibrillators, Implantable", "Pacemaker, Artificial",
            "Cardiac Resynchronization Therapy", "Tachycardia, Ventricular",
            "Death, Sudden, Cardiac", "Bundle-Branch Block",
        ],
    },
    {
        "id": "insuficiencia-cardiaca",
        "name": "Insuficiencia Cardíaca",
        "color": "#2471A3",
        "keywords": [
            "heart failure", "cardiac failure", "hfref", "hfpef", "hfmref",
            "reduced ejection fraction", "preserved ejection fraction",
            "mildly reduced ejection fraction", "left ventricular dysfunction",
            "sglt2 inhibitor", "dapagliflozin", "empagliflozin", "sotagliflozin",
            "sacubitril", "valsartan", "angiotensin", "ivabradine", "finerenone",
            "ventricular assist device", "lvad", "heart transplantation",
            "acute decompensated heart failure", "cardiorenal syndrome",
            "remote monitoring", "pulmonary artery pressure", "cardiomems",
            "diuretic resistance", "natriuretic peptide", "bnp", "nt-probnp",
        ],
        "mesh": [
            "Heart Failure", "Ventricular Dysfunction, Left",
            "Sodium-Glucose Transporter 2 Inhibitors", "Heart-Assist Devices",
            "Heart Transplantation", "Mineralocorticoid Receptor Antagonists",
        ],
    },
    {
        "id": "isquemica",
        "name": "Cardiopatía Isquémica",
        "color": "#D35400",
        "keywords": [
            "myocardial infarction", "stemi", "nstemi", "acute coronary syndrome",
            "unstable angina", "coronary artery disease", "ischemic heart disease",
            "cardiogenic shock", "cardiac arrest", "out-of-hospital cardiac arrest",
            "ticagrelor", "prasugrel", "clopidogrel", "dual antiplatelet", "dapt",
            "p2y12", "antiplatelet therapy", "fibrinolysis", "thrombolysis",
            "culprit lesion", "complete revascularization", "chronic coronary syndrome",
            "stable angina", "door-to-balloon",
        ],
        "mesh": [
            "Myocardial Infarction", "Acute Coronary Syndrome", "Coronary Artery Disease",
            "Angina, Unstable", "Platelet Aggregation Inhibitors", "Shock, Cardiogenic",
            "Heart Arrest",
        ],
    },
    {
        "id": "prevencion",
        "name": "Prevención y Dislipidemia",
        "color": "#1E8449",
        "keywords": [
            "primary prevention", "secondary prevention", "dyslipidemia",
            "hypercholesterolemia", "statin", "atorvastatin", "rosuvastatin",
            "ldl cholesterol", "ldl-c", "pcsk9", "inclisiran", "evolocumab",
            "alirocumab", "ezetimibe", "bempedoic acid", "omega-3",
            "cardiovascular risk", "risk stratification", "atherosclerosis",
            "inflammation", "c-reactive protein", "hsCRP", "colchicine",
            "aspirin", "polygenic risk score", "metabolic syndrome",
            "obesity cardiovascular", "diabetes cardiovascular", "ziltivekimab",
        ],
        "mesh": [
            "Dyslipidemias", "Hydroxymethylglutaryl-CoA Reductase Inhibitors",
            "PCSK9 Inhibitors", "Primary Prevention", "Secondary Prevention",
            "Atherosclerosis", "Cholesterol, LDL",
        ],
    },
    {
        "id": "valvulopatias",
        "name": "Valvulopatías",
        "color": "#148F77",
        "keywords": [
            "valvular heart disease", "aortic stenosis", "aortic regurgitation",
            "mitral regurgitation", "mitral stenosis", "tricuspid regurgitation",
            "tricuspid stenosis", "infective endocarditis", "transcatheter mitral",
            "mitraclip", "edge-to-edge repair", "teer", "aortic valve replacement",
            "mitral valve repair", "mitral valve replacement", "valve surgery",
            "bioprosthesis", "mechanical valve", "prosthetic valve",
            "paravalvular leak", "transcatheter tricuspid",
        ],
        "mesh": [
            "Heart Valve Diseases", "Aortic Valve Stenosis", "Mitral Valve Insufficiency",
            "Endocarditis, Bacterial", "Heart Valve Prosthesis",
            "Tricuspid Valve Insufficiency", "Aortic Valve Insufficiency",
        ],
    },
    {
        "id": "hipertension",
        "name": "Hipertensión Arterial",
        "color": "#922B21",
        "keywords": [
            "arterial hypertension", "blood pressure control", "antihypertensive",
            "renal denervation", "resistant hypertension", "ambulatory blood pressure",
            "isolated systolic hypertension", "white coat hypertension",
            "renin-angiotensin-aldosterone", "ace inhibitor", "angiotensin receptor blocker",
            "calcium channel blocker", "mineralocorticoid antagonist",
            "aldosterone", "zilebesiran", "sirna hypertension",
            "hypertensive heart disease", "hypertensive nephropathy",
        ],
        "mesh": [
            "Hypertension", "Blood Pressure", "Antihypertensive Agents",
            "Denervation", "Aldosterone", "Renin-Angiotensin System",
        ],
    },
    {
        "id": "miocardiopatias",
        "name": "Miocardiopatías",
        "color": "#6C3483",
        "keywords": [
            "hypertrophic cardiomyopathy", "hcm", "dilated cardiomyopathy",
            "arrhythmogenic cardiomyopathy", "arvc", "cardiac amyloidosis",
            "transthyretin amyloidosis", "attr", "al amyloid", "mavacamten",
            "aficamten", "tafamidis", "acoramidis", "myocarditis",
            "cardiac sarcoidosis", "fabry disease", "lamin a/c",
            "noncompaction cardiomyopathy", "takotsubo", "stress cardiomyopathy",
            "genetic cardiomyopathy", "myocardial fibrosis",
        ],
        "mesh": [
            "Cardiomyopathies", "Cardiomyopathy, Hypertrophic",
            "Cardiomyopathy, Dilated", "Amyloidosis", "Myocarditis",
        ],
    },
    {
        "id": "imagen",
        "name": "Imagen Cardíaca",
        "color": "#117A65",
        "keywords": [
            "cardiac imaging", "echocardiography", "cardiac mri",
            "cardiac magnetic resonance", "cardiac computed tomography",
            "coronary ct angiography", "cardiac pet", "myocardial perfusion",
            "spect", "t1 mapping", "t2 mapping", "extracellular volume",
            "strain imaging", "global longitudinal strain",
            "three-dimensional echocardiography", "transesophageal echocardiography",
            "artificial intelligence imaging", "deep learning echocardiography",
            "multimodality cardiac imaging",
        ],
        "mesh": [
            "Echocardiography", "Magnetic Resonance Imaging",
            "Tomography, X-Ray Computed", "Coronary Angiography",
            "Myocardial Perfusion Imaging",
        ],
    },
    {
        "id": "estructural",
        "name": "Cardiología Estructural",
        "color": "#1A5276",
        "keywords": [
            "structural heart disease", "left atrial appendage closure",
            "laa closure", "watchman", "patent foramen ovale", "pfo closure",
            "atrial septal defect closure", "asd", "ventricular septal defect",
            "paravalvular leak closure", "alcohol septal ablation",
            "hypertrophic obstructive cardiomyopathy obstruction",
            "transcatheter pulmonary valve", "congenital heart adult",
        ],
        "mesh": [
            "Heart Septal Defects, Atrial", "Foramen Ovale, Patent",
            "Atrial Appendage", "Heart Defects, Congenital",
        ],
    },
    {
        "id": "oncologia-cardiaca",
        "name": "Oncología Cardíaca",
        "color": "#BA4A00",
        "keywords": [
            "cardio-oncology", "cardiooncology", "cardiotoxicity",
            "anthracycline cardiotoxicity", "immune checkpoint inhibitor cardiac",
            "myocarditis immune checkpoint", "cancer cardiovascular risk",
            "chemotherapy cardiac", "radiation therapy cardiac",
            "trastuzumab cardiac", "cardioprotection chemotherapy",
            "cancer survivor cardiovascular", "hematologic malignancy cardiac",
        ],
        "mesh": [
            "Cardiotoxicity", "Antineoplastic Agents",
        ],
    },
]

SUB_INDEX = {s["id"]: s for s in SUBSPECIALTIES}


# ────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ────────────────────────────────────────────────────────────────────────────

def http_get(url, params=None, timeout=30):
    full_url = url + ("?" + urlencode(params) if params else "")
    req = Request(full_url, headers={
        "User-Agent": "BriefingCardiologico/2.0 (https://github.com/joelserpa15-eng/Briefing-cardiol-gico-)"
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (URLError, HTTPError) as e:
        print(f"    [WARN] HTTP error for {full_url[:80]}…: {e}", file=sys.stderr)
        return None


def ncbi_get(url, params):
    """NCBI E-utilities call with mandatory email + optional API key."""
    params = dict(params)
    params["email"] = NCBI_EMAIL
    params["tool"]  = "BriefingCardiologico"
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    result = http_get(url, params)
    time.sleep(NCBI_DELAY)
    return result


# ────────────────────────────────────────────────────────────────────────────
# PubMed search
# ────────────────────────────────────────────────────────────────────────────

def build_pubmed_query(date_start, date_end):
    journal_clause = " OR ".join(f'"{j["nlm"]}"[Journal]' for j in JOURNALS)
    evidence_clause = (
        'meta-analysis[pt] OR "systematic review"[pt] OR '
        '"randomized controlled trial"[pt] OR "controlled clinical trial"[pt] OR '
        '"clinical trial"[pt] OR "multicenter study"[pt] OR '
        '"observational study"[pt]'
    )
    cardio_clause = (
        '"cardiovascular diseases"[MeSH] OR "heart diseases"[MeSH] OR '
        '"coronary"[tiab] OR "cardiac"[tiab] OR "cardio"[tiab] OR '
        '"atrial fibrillation"[tiab] OR "myocardial"[tiab] OR '
        '"hypertension"[tiab] OR "heart failure"[tiab] OR '
        '"arrhythmia"[tiab] OR "stroke"[tiab]'
    )
    return (
        f"({journal_clause}) AND ({evidence_clause}) "
        f"AND ({cardio_clause}) "
        f"AND {date_start}:{date_end}[pdat]"
    )


def pubmed_search(date_start, date_end, max_results=300):
    query = build_pubmed_query(date_start, date_end)
    print(f"  Query dates: {date_start} → {date_end}")
    data = ncbi_get(ESEARCH_URL, {
        "db": "pubmed", "term": query,
        "retmax": max_results, "retmode": "json", "sort": "relevance",
    })
    if not data:
        return []
    result = json.loads(data)
    ids = result.get("esearchresult", {}).get("idlist", [])
    print(f"  PubMed: {len(ids)} PMIDs found")
    return ids


def pubmed_fetch(pmids, batch_size=20):
    articles = []
    batches = [pmids[i:i+batch_size] for i in range(0, len(pmids), batch_size)]
    for idx, batch in enumerate(batches):
        xml = ncbi_get(EFETCH_URL, {
            "db": "pubmed", "id": ",".join(batch),
            "retmode": "xml", "rettype": "abstract",
        })
        if xml:
            parsed = parse_pubmed_xml(xml)
            articles.extend(parsed)
            print(f"  Fetched batch {idx+1}/{len(batches)} → {len(parsed)} parsed")
        else:
            print(f"  [WARN] Batch {idx+1} failed", file=sys.stderr)
    return articles


def parse_pubmed_xml(xml_text):
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"  [WARN] XML parse error: {exc}", file=sys.stderr)
        return []

    for pa in root.findall(".//PubmedArticle"):
        art = _parse_one(pa)
        if art:
            articles.append(art)
    return articles


def _parse_one(pa):
    medline = pa.find("MedlineCitation")
    if medline is None:
        return None

    pmid_el = medline.find("PMID")
    pmid = pmid_el.text.strip() if pmid_el is not None else ""

    article = medline.find("Article")
    if article is None:
        return None

    # ── Title ──
    title_el = article.find("ArticleTitle")
    title = re.sub(r'\s+', ' ', "".join(title_el.itertext())).strip().rstrip('.') if title_el is not None else ""
    if not title:
        return None

    # ── Abstract ──
    abstract = _extract_abstract(article)
    if not abstract:
        return None  # Articles without abstract are skipped

    # ── Authors ──
    author_str = _extract_authors(article)

    # ── Journal & year ──
    journal_abbr, journal_display, year = _extract_journal(article)

    # ── DOI / URL ──
    doi = ""
    for id_el in pa.findall(".//ArticleId"):
        if id_el.get("IdType") == "doi":
            doi = (id_el.text or "").strip()
            break
    url = f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    doi_display = doi if doi else f"PMID:{pmid}"

    # ── Evidence level ──
    pub_types = [pt.text for pt in article.findall("PublicationTypeList/PublicationType") if pt.text]
    ev_rank, ev_label = get_evidence(pub_types)

    # ── MeSH + keywords ──
    mesh = [mh.findtext("DescriptorName", "") for mh in medline.findall("MeshHeadingList/MeshHeading")]
    kws  = [kw.text for kw in medline.findall("KeywordList/Keyword") if kw.text]

    # ── Journal rank ──
    j_rank = _journal_rank(journal_abbr, journal_display)

    return {
        "pmid":            pmid,
        "title":           title,
        "journal":         journal_display,
        "journalAbbr":     journal_abbr,
        "journalRank":     j_rank,
        "authors":         author_str,
        "year":            year,
        "doi":             doi_display,
        "url":             url,
        "abstract":        abstract,
        "evidenceLevel":   ev_label,
        "evidenceRank":    ev_rank,
        "meshTerms":       mesh,
        "keywords":        kws,
        "keyFindings":     "",
        "source":          "PubMed",
    }


def _extract_abstract(article):
    abstract_el = article.find("Abstract")
    if abstract_el is None:
        return ""
    parts = []
    for text_el in abstract_el.findall("AbstractText"):
        label = text_el.get("Label", "")
        text  = "".join(text_el.itertext()).strip()
        if text:
            parts.append(f"{label + ': ' if label else ''}{text}")
    return " ".join(parts).strip()


def _extract_authors(article):
    author_list = article.find("AuthorList")
    if author_list is None:
        return "Authors not available"
    names = []
    for auth in author_list.findall("Author"):
        last    = auth.findtext("LastName", "")
        initials = auth.findtext("Initials", "")
        if last:
            names.append(f"{last} {initials}".strip())
    if not names:
        return "Authors not available"
    display = ", ".join(names[:4])
    if len(names) > 4:
        display += ", et al."
    return display


def _extract_journal(article):
    j_el = article.find("Journal")
    if j_el is None:
        return "", "", datetime.now().year
    abbr    = (j_el.findtext("ISOAbbreviation") or "").strip()
    full    = (j_el.findtext("Title") or abbr).strip()
    display = NLM_TO_DISPLAY.get(abbr, full)
    # Year
    pub = j_el.find("JournalIssue/PubDate")
    year = datetime.now().year
    if pub is not None:
        yr_el = pub.find("Year")
        if yr_el is not None:
            try:
                year = int(yr_el.text)
            except ValueError:
                pass
        else:
            m = re.match(r'(\d{4})', pub.findtext("MedlineDate", ""))
            if m:
                year = int(m.group(1))
    return abbr, display, year


def _journal_rank(abbr, full_name):
    if abbr in NLM_TO_RANK:
        return NLM_TO_RANK[abbr]
    al = abbr.lower()
    fl = full_name.lower()
    for j in JOURNALS:
        jl = j["nlm"].lower()
        if jl in al or al in jl or j["display"].lower() in fl:
            return j["rank"]
    return 99


# ────────────────────────────────────────────────────────────────────────────
# Europe PMC secondary search
# ────────────────────────────────────────────────────────────────────────────

def epmc_search(date_start, date_end, existing_dois):
    """Fetch additional articles from Europe PMC not already in PubMed results."""
    journal_q = " OR ".join(f'JOURNAL:"{j["nlm"]}"' for j in JOURNALS[:12])  # Top 12 only
    date_q    = f'FIRST_PDATE:[{date_start.replace("/","-")} TO {date_end.replace("/","-")}]'
    pub_q     = ('PUB_TYPE:"meta-analysis" OR PUB_TYPE:"systematic-review" OR '
                 'PUB_TYPE:"research-article" OR PUB_TYPE:"randomized-controlled-trial"')
    query = f'({journal_q}) AND ({pub_q}) AND ({date_q})'

    params = {
        "query":      query,
        "format":     "json",
        "pageSize":   100,
        "resultType": "core",
        "sort":       "RELEVANCE",
    }
    raw = http_get(EPMC_URL, params)
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    articles = []
    for r in data.get("resultList", {}).get("result", []):
        doi = r.get("doi", "")
        if doi and doi in existing_dois:
            continue  # Already have this article from PubMed

        title    = r.get("title", "").rstrip('.')
        abstract = r.get("abstractText", "")
        if not title or not abstract:
            continue

        authors_raw = r.get("authorString", "")
        authors = authors_raw[:100] + ("…" if len(authors_raw) > 100 else "")

        journal_abbr = r.get("journalAbbreviation", r.get("journalTitle", ""))
        journal_full = r.get("journalTitle", journal_abbr)
        j_rank       = _journal_rank(journal_abbr, journal_full)

        year_str = r.get("pubYear", str(datetime.now().year))
        try:
            year = int(year_str)
        except ValueError:
            year = datetime.now().year

        pub_types = [pt.get("pubType", "") for pt in r.get("pubTypeList", {}).get("pubType", [])]
        ev_rank, ev_label = get_evidence(pub_types)

        url = f"https://doi.org/{doi}" if doi else r.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url", "#")
        doi_display = doi if doi else f"PMID:{r.get('pmid','')}"

        articles.append({
            "pmid":          r.get("pmid", ""),
            "title":         title,
            "journal":       NLM_TO_DISPLAY.get(journal_abbr, journal_full),
            "journalAbbr":   journal_abbr,
            "journalRank":   j_rank,
            "authors":       authors,
            "year":          year,
            "doi":           doi_display,
            "url":           url,
            "abstract":      abstract,
            "evidenceLevel": ev_label,
            "evidenceRank":  ev_rank,
            "meshTerms":     [],
            "keywords":      r.get("keywordList", {}).get("keyword", []),
            "keyFindings":   "",
            "source":        "EuropePMC",
        })

    time.sleep(0.5)
    print(f"  Europe PMC: {len(articles)} additional articles found")
    return articles


# ────────────────────────────────────────────────────────────────────────────
# Subspecialty classification
# ────────────────────────────────────────────────────────────────────────────

def classify(article):
    title    = article.get("title", "").lower()
    abstract = article.get("abstract", "").lower()
    mesh_set = {m.lower() for m in article.get("meshTerms", [])}
    kw_set   = {k.lower() for k in article.get("keywords", [])}

    scores = {}
    for sub in SUBSPECIALTIES:
        score = 0
        for kw in sub["keywords"]:
            kl = kw.lower()
            if kl in title:
                score += 2
            elif kl in abstract:
                score += 1
        for mesh in sub["mesh"]:
            if mesh.lower() in mesh_set:
                score += 3
        # keyword list bonus
        for kw in sub["keywords"]:
            if kw.lower() in kw_set:
                score += 1
        scores[sub["id"]] = score

    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


# ────────────────────────────────────────────────────────────────────────────
# Key findings extraction
# ────────────────────────────────────────────────────────────────────────────

CONCLUSION_MARKERS = [
    "conclusion", "conclusions", "in conclusion", "in summary",
    "our findings", "we found", "results show", "significantly",
    "primary endpoint", "primary outcome", "this trial", "this study",
    "demonstrated", "reduced", "improved", "superior", "non-inferior",
    "this analysis", "these results",
]


def extract_key_finding(abstract):
    # Try to find a CONCLUSIONS section (structured abstract)
    m = re.search(
        r'(?:CONCLUSIONS?|INTERPRETATION|SIGNIFICANCE)[:\s]+(.+?)(?=\s+[A-Z]{3,}:|$)',
        abstract,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        text = m.group(1).strip()[:500]
        if len(text) > 30:
            return text

    # Fallback: last sentence that contains a conclusion marker
    sentences = re.split(r'(?<=[.!?])\s+', abstract)
    for sentence in reversed(sentences):
        sl = sentence.lower()
        if any(marker in sl for marker in CONCLUSION_MARKERS):
            s = sentence.strip()
            if 30 < len(s) < 500:
                return s

    # Last resort: last non-trivial sentence
    for sentence in reversed(sentences):
        s = sentence.strip()
        if 40 < len(s) < 500:
            return s

    return abstract[:300] + "…"


# ────────────────────────────────────────────────────────────────────────────
# Clinical impact scoring
# ────────────────────────────────────────────────────────────────────────────

JOURNAL_IF_MAP = {j["display"].lower(): j["if"] for j in JOURNALS}
JOURNAL_IF_MAP.update({j["nlm"].lower(): j["if"] for j in JOURNALS})

def compute_clinical_impact(article):
    """Estimate probability (0–100) of influencing clinical practice."""
    # Base score by evidence level
    base = {1: 75, 2: 55, 3: 60, 4: 30, 5: 20, 6: 10, 7: 5}
    score = base.get(article.get("evidenceRank", 7), 5)

    # Journal IF modifier
    jname = article.get("journal", "").lower()
    jabbr = article.get("journalAbbr", "").lower()
    journal_if = 0
    for key, ifval in JOURNAL_IF_MAP.items():
        if key in jname or key in jabbr:
            journal_if = ifval
            break
    if journal_if >= 50:
        score += 15
    elif journal_if >= 20:
        score += 8
    elif journal_if >= 7:
        score += 4

    # Analyse abstract + keyFindings for signals
    text = (article.get("abstract", "") + " " + article.get("keyFindings", "")).lower()

    # Positive endpoint signals → practice change more likely
    if any(p in text for p in ["p<0.0", "p=0.0", "redujo", "reduci", "superior",
                                "non-inferior", "no inferior", "significantly reduced",
                                "significativamente"]):
        score += 8

    # Underpowered / inconclusive penalty
    if any(p in text for p in ["infra-potenciad", "infraestimad", "underpowered",
                                "no alcanzó significación", "no fue significativ",
                                "not significant", "did not reach significance"]):
        score -= 15

    # Large sample bonus
    for pat in ["n=", "(n ="]:
        idx = text.find(pat)
        if idx >= 0:
            num = ""
            for ch in text[idx + len(pat):]:
                if ch.isdigit():
                    num += ch
                elif ch == ",":
                    pass
                else:
                    break
            try:
                n = int(num)
                if n >= 5000:
                    score += 7
                elif n >= 1000:
                    score += 4
                elif n >= 300:
                    score += 2
            except ValueError:
                pass
            break

    score = max(0, min(100, score))

    if score >= 70:
        label = "Alta"
    elif score >= 40:
        label = "Moderada"
    else:
        label = "Baja"

    # Build short rationale
    ev_names = {1: "Meta-análisis", 2: "Revisión sistemática", 3: "RCT",
                4: "Estudio de cohorte", 5: "Caso-control", 6: "Serie de casos", 7: "Estudio"}
    ev_str = ev_names.get(article.get("evidenceRank", 7), "Estudio")
    journal_str = article.get("journal", "revista indexada")
    if label == "Alta":
        rationale = (f"{ev_str} publicado en {journal_str} con resultado positivo "
                     f"en un escenario clínico común — alta probabilidad de modificar la práctica.")
    elif label == "Moderada":
        rationale = (f"{ev_str} en {journal_str} — puede influir en la práctica "
                     f"de centros especializados o en actualizaciones de guías a medio plazo.")
    else:
        rationale = (f"{ev_str} con evidencia limitada o resultado no concluyente "
                     f"— impacto inmediato en práctica clínica reducido.")

    return {"score": score, "label": label, "rationale": rationale}


# ────────────────────────────────────────────────────────────────────────────
# Utility: week label in Spanish
# ────────────────────────────────────────────────────────────────────────────

MONTHS_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def week_info(today=None):
    today = today or datetime.utcnow()
    start = today - timedelta(days=today.weekday())
    end   = start + timedelta(days=6)
    if start.month == end.month:
        label = f"{start.day} – {end.day} de {MONTHS_ES[start.month]}, {end.year}"
    else:
        label = (f"{start.day} de {MONTHS_ES[start.month]} – "
                 f"{end.day} de {MONTHS_ES[end.month]}, {end.year}")
    week_id = f"{today.year}-W{today.strftime('%W').zfill(2)}"
    return week_id, label


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch weekly cardiology articles")
    parser.add_argument("--days", type=int, default=14,
                        help="Days to look back (default 14 — ensures enough content)")
    args = parser.parse_args()

    today      = datetime.utcnow()
    date_end   = today.strftime("%Y/%m/%d")
    date_start = (today - timedelta(days=args.days)).strftime("%Y/%m/%d")
    week_id, week_label = week_info(today)

    print("=" * 60)
    print("  Briefing Cardiológico — Weekly Article Fetcher")
    print("=" * 60)
    print(f"  Week  : {week_label}")
    print(f"  Range : {date_start} → {date_end}")
    print(f"  Email : {NCBI_EMAIL}")
    print(f"  API   : {'YES' if NCBI_API_KEY else 'no (3 req/s limit)'}")
    print()

    # ── 1. PubMed ──────────────────────────────────────────────────────────
    print("[1/4] Searching PubMed…")
    pmids = pubmed_search(date_start, date_end)
    if not pmids:
        print("  No PMIDs found — keeping existing data.")
        sys.exit(0)

    print(f"\n[2/4] Fetching PubMed article details ({len(pmids)} PMIDs)…")
    raw = pubmed_fetch(pmids)
    print(f"  Parsed: {len(raw)} articles with abstracts")

    # ── 2. Europe PMC (secondary) ───────────────────────────────────────────
    print("\n[3/4] Querying Europe PMC for additional articles…")
    existing_dois = {a["doi"] for a in raw if not a["doi"].startswith("PMID")}
    epmc_articles = epmc_search(date_start, date_end, existing_dois)
    raw.extend(epmc_articles)
    print(f"  Total pool: {len(raw)} articles")

    # ── 3. Classify ────────────────────────────────────────────────────────
    print("\n[4/4] Classifying and ranking articles…")
    buckets = {s["id"]: [] for s in SUBSPECIALTIES}

    for art in raw:
        sub_id = classify(art)
        if not sub_id:
            continue
        art["keyFindings"] = extract_key_finding(art["abstract"])
        clean = {
            "id":            f"{sub_id}-{art['pmid'] or art['doi'][:20].replace('/','_')}",
            "title":         art["title"],
            "journal":       art["journal"],
            "authors":       art["authors"],
            "year":          art["year"],
            "doi":           art["doi"],
            "url":           art["url"],
            "evidenceLevel": art["evidenceLevel"],
            "evidenceRank":  art["evidenceRank"],
            "journalRank":   art["journalRank"],
            "abstract":      art["abstract"],
            "keyFindings":   art["keyFindings"],
            "source":        art["source"],
            "clinicalImpact": compute_clinical_impact(art),
        }
        buckets[sub_id].append(clean)

    # ── 4. Build output ────────────────────────────────────────────────────
    output_subs = []
    total = 0
    for sub in SUBSPECIALTIES:
        arts = buckets[sub["id"]]
        # Sort: evidence rank ASC, then journal rank ASC, then year DESC
        arts.sort(key=lambda a: (a["evidenceRank"], a["journalRank"], -a["year"]))
        # Remove internal sort key
        for a in arts:
            a.pop("journalRank", None)
        if arts:
            output_subs.append({
                "id":       sub["id"],
                "name":     sub["name"],
                "color":    sub["color"],
                "articles": arts,
            })
            total += len(arts)

    all_arts    = [a for s in output_subs for a in s["articles"]]
    n_meta      = sum(1 for a in all_arts if a["evidenceRank"] == 1)
    n_sr        = sum(1 for a in all_arts if a["evidenceRank"] == 2)
    n_rct       = sum(1 for a in all_arts if a["evidenceRank"] == 3)

    output = {
        "week":        week_id,
        "weekLabel":   week_label,
        "lastUpdated": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stats": {
            "total":       total,
            "metaAnalysis": n_meta,
            "systematicReview": n_sr,
            "rct":         n_rct,
        },
        "subspecialties": output_subs,
    }

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)

    # ── Save current week (main file, always up to date)
    out_path = os.path.join(data_dir, "articles.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ── Save archive copy for this week
    archive_name = f"articles-{week_id.replace('/', '-')}.json"
    archive_path = os.path.join(data_dir, archive_name)
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ── Update index.json (keep last 4 weeks)
    index_path = os.path.join(data_dir, "index.json")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            idx_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        idx_data = {"current": "", "weeks": []}

    new_entry = {"id": week_id, "label": week_label, "file": archive_name}
    existing_ids = [w["id"] for w in idx_data.get("weeks", [])]
    if week_id not in existing_ids:
        idx_data["weeks"].insert(0, new_entry)
        idx_data["weeks"] = idx_data["weeks"][:4]  # keep max 4 weeks
    else:
        # Update entry in case label changed
        for w in idx_data["weeks"]:
            if w["id"] == week_id:
                w.update(new_entry)
    idx_data["current"] = week_id

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(idx_data, f, ensure_ascii=False, indent=2)

    print(f"\n  Saved {total} articles across {len(output_subs)} subspecialties")
    print(f"  Meta-análisis: {n_meta}  |  Rev. sistemáticas: {n_sr}  |  RCT: {n_rct}")
    print(f"  Output → {out_path}")
    print("=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
