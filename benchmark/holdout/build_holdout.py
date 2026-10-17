# -*- coding: utf-8 -*-
"""Build the frozen improvement holdout (300 queries + 100 extraction pages).

Written BEFORE any ranking/intent tuning in the improvement programme, so the
queries below were not selected against tuned behaviour. Run once:

    .venv/bin/python benchmark/holdout/build_holdout.py

Outputs queries_holdout.jsonl / extraction_pages.jsonl next to this file.
The SHA-256 of queries_holdout.jsonl is recorded in README.md in this folder.
Do not edit the generated queries after freezing; add a new holdout instead.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

CATS = {
    1: "Current events / recent developments",
    2: "Programming docs / technical troubleshooting",
    3: "General factual",
    4: "Science / scientific sources",
    5: "Niche / long-tail",
    6: "Exact-site / domain-restricted",
    7: "Multi-source research / cross-checking",
    8: "Business / market / product research",
    9: "Multilingual / non-English",
    10: "Ambiguous / complex / multi-step",
}

QUERIES: dict[int, list[str]] = {
    1: [
        "Winter Paralympics 2026 medal standings final", "Chandrayaan-4 mission preparation status 2026",
        "India AI Impact Summit 2026 key outcomes", "Assam panchayat elections 2026 results",
        "Karnataka state budget 2026-27 highlights", "Odisha state budget 2026-27 highlights",
        "NEET PG 2026 counselling schedule latest", "UPSC civil services 2026 notification vacancies",
        "Assam HSLC result 2026 declaration date", "India England Test series 2026 schedule venues",
        "Indian Super League 2026-27 season start date", "Guwahati smart city projects October 2026 update",
        "Brahmaputra ferry service new routes 2026", "India smartphone exports September 2026 growth",
        "CBSE class 10 board exam 2027 datesheet", "Kerala lottery result October 2026 today",
        "IMD winter forecast India December 2026", "India forex reserves weekly October 2026 RBI",
        "Nifty 50 earnings season Q2 FY27 outlook", "Assam cabinet expansion October 2026 news",
        "Delhi pollution GRAP stage November 2026", "Mumbai local train mega block October 2026",
        "Bengaluru metro Yellow Line opening 2026", "Hyderabad metro phase 2 approval 2026",
        "Chennai metro phase 2 corridor progress 2026", "Kolkata metro East West corridor 2026 update",
        "Pune metro line 3 Hinjewadi opening 2026", "Lucknow Kanpur expressway opening 2026",
        "Vande Bharat sleeper launch 2026 routes", "India 6G testbed launch 2026 news",
    ],
    2: [
        "pandas FutureWarning downcasting object dtype fillna fix", "Node.js ERR_MODULE_NOT_FOUND tsx ESM loader fix",
        "Python asyncio timeout context manager example 3.11", "React Server Components client component boundary error",
        "PostgreSQL invalid page in block vacuum warning", "MySQL error 1452 foreign key constraint fails insert",
        "Docker compose bind mount file changed inode stale", "Kubernetes ImagePullBackOff private registry secret fix",
        "nginx upstream sent too big header while reading response", "git fatal refusing to merge unrelated histories fix",
        "TypeScript Argument of type string not assignable to never", "Rust cannot borrow as mutable more than once axum handler",
        "Go interface conversion interface is nil not error panic", "Java OutOfMemoryError Metaspace classloader leak diagnosis",
        "Spring Data JPA N+1 fetch join pagination warning", "Django MigrationSchemaMissing migration history fix",
        "FastAPI 422 validation error request body debugging", "SQLAlchemy DetachedInstanceError lazy load session fix",
        "Redis WRONGTYPE operation against a key holding wrong kind", "Kafka consumer commits offset on rebalance duplicate processing",
        "Elasticsearch circuit_breaking_exception parent data too large", "MongoDB cursor not found aggregation pipeline fix",
        "Celery task always reserved worker not executing debug", "pytest fixture scope mismatch ScopeMismatch error",
        "Vite build chunk size warning code splitting manualChunks", "Webpack Module not found Can't resolve fs in browser",
        "Terraform Error acquiring the state lock ConditionalCheckFailed", "Ansible undefined variable in when conditional fix",
        "bash bad substitution in parameter expansion script", "PowerShell execution policy RemoteSigned script blocked fix",
    ],
    3: [
        "capital of Mongolia", "who wrote the Ramcharitmanas", "largest mammal by length",
        "currency used in Vietnam", "how many districts are in Kerala",
        "tallest waterfall in India by height", "which gas makes soda fizzy",
        "who was the first woman judge of Supreme Court of India", "national tree of India",
        "which metal is liquid at room temperature apart from mercury", "longest beach in India",
        "how many spokes are in the Ashoka Chakra", "smallest state of India by population",
        "which river is called Dakshin Ganga", "author of Godan novel",
        "how many fundamental duties are in Indian Constitution", "largest freshwater lake in India",
        "which planet rotates clockwise as seen from above", "inventor of the diesel engine",
        "how many bones are in a giraffe neck", "capital city of Bhutan",
        "which Indian state has the longest coastline among peninsular states", "main ingredient of guacamole",
        "how many players are on a kabaddi team on court", "currency of Sri Lanka",
        "which organ produces insulin in the human body", "largest island of India by area",
        "who designed the Indian Parliament House", "how many years is one term of Rajya Sabha member",
        "which chemical gives turmeric its yellow colour",
    ],
    4: [
        "black hole shadow M87 diameter measurement update", "ocean alkalinity enhancement carbon removal trial results",
        "perovskite silicon tandem cell certified efficiency record", "human gut virome diversity cohort study findings",
        "el Nino southern oscillation forecast accuracy seasonal", "CRISPR prime editing in vivo liver trial update",
        "fast radio burst repeating source localisation study", "microplastic removal wastewater treatment efficiency study",
        "heat stress kidney disease agricultural workers cohort", "dengue NS1 antigen test sensitivity field evaluation",
        "coral bleaching thermal stress degree heating weeks", " methane emissions from rice paddies mitigation alternate wetting",
        "solid state lithium metal anode dendrite suppression study", "mRNA influenza vaccine phase 2 immunogenicity results",
        "sleep glymphatic clearance human imaging evidence", "soil microbiome nitrogen fixation non-legume crops study",
        "exoplanet K2-18b atmosphere reanalysis 2026", "gravitational wave neutron star merger kilonova follow-up",
        "Arctic permafrost mercury release river flux estimate", "urban tree canopy temperature reduction systematic review",
        "antibiotic resistance wastewater surveillance India study", "tuberculosis shorter regimen BPaL real world outcomes",
        "vitamin B12 deficiency vegetarian cohort India prevalence", "air pollution dementia risk longitudinal cohort evidence",
        "bee pollinator decline pesticide mixture field study", "glacier retreat Hindu Kush Himalaya mass balance update",
        "deep sea hydrothermal vent microbial ecosystem study", "fusion plasma confinement EAST tokamak record 2026",
        "quantum dot solar cell stability outdoor testing", "CAR T cell therapy autoimmune disease early trial",
    ],
    5: [
        "Dhubri Phulbari bridge construction completion target", "Assam wooden boat country boat GI craft clusters",
        "Barak Valley tea estates list Silchar Cachar", "Bodo Sahitya Sabha annual conference venue history",
        "Assamese gamusa handloom production centres", "Majuli mask making Samaguri satra history",
        "Sualkuchi Mekhela Chador price handloom vs powerloom", "Goalpara Goalpariya folk song performers list",
        "Nagaon Raha fish seed production centre Assam", "Assam lemon Kaji Nemu GI tag year",
        "one district one product Assam list Barpeta", "Assam agarwood cultivation legal permission process",
        "Dima Hasao Haflong tourist places list", "Umrangso hot spring Assam location",
        "Panimur waterfall Dima Hasao entry fee", "Haflong lake boating timing",
        "Jatinga bird mystery season months Assam", "Chandubi festival January dates Assam",
        "Jonbeel Mela barter system Assam history", "Ambubachi Mela Kamakhya devotees count estimate",
        "Bathou Puja Bodo ritual significance", "Ali Ai Ligang Mishing festival date",
        "Me-Dam-Me-Phi Ahom festival observance", "Rongali Bihu Bohag date Assamese new year",
        "Assam direct recruitment grade 3 merit list district wise", "Sewa Setu Assam services list certificates",
        "Orunodoi scheme beneficiary list check Assam", "Assam Mukhyamantri Atmanirbhar Asom eligibility",
        "PM Vishwakarma Assam artisan registration centres", "Assam Skill University Mangaldai courses list",
    ],
    6: [
        "site:docs.djangoproject.com aggregation annotate queryset", "site:developer.chrome.com lighthouse performance audits",
        "site:learn.microsoft.com azure functions cold start", "site:cloud.google.com cloud run concurrency requests",
        "site:docs.aws.amazon.com lambda environment variables encryption", "site:postgresql.org pg_stat_statements configuration",
        "site:dev.mysql.com partition pruning optimizer", "site:redis.io latency monitor command",
        "site:kubernetes.io pod disruption budget minAvailable", "site:helm.sh chart template functions sprig",
        "site:istio.io authorization policy JWT validation", "site:prometheus.io alertmanager inhibition rules",
        "site:grafana.com loki log queries LogQL", "site:opentelemetry.io collector processors batch",
        "site:nginx.org proxy cache lock directive", "site:apache.org httpd mod_proxy balancer",
        "site:git-scm.com git worktree documentation", "site:docs.github.com codeql default setup",
        "site:gitlab.com ci cache key files", "site:docs.docker.com buildx bake reference",
        "site:podman.io rootless networking pasta", "site:vagrantup.com synced folders rsync",
        "site:terraform.io state remote backend locking", "site:ansible.com playbook handlers flush",
        "site:puppet.com code management r10k", "site:saltproject.io state requisites watch",
        "site:docs.python.org zipfile path traversal", "site:peps.python.org PEP 681 dataclass transforms",
        "site:rfc-editor.org RFC 9113 HTTP/2 settings", "site:w3.org ARIA live regions guidance",
    ],
    7: [
        "NEET vs JEE difficulty comparison student outcomes evidence", "work from home vs office productivity Indian IT studies",
        "UPI vs cards merchant discount rate comparison India", "solar rooftop subsidy net metering state comparison India",
        "EV scooter total cost ownership vs petrol scooter 5 years India", "index funds vs ETFs expense ratio tracking error India",
        "term insurance vs endowment policy returns comparison India", "old vs new tax regime salaried 18 lakh comparison FY27",
        "PPF vs ELSS long term returns risk comparison", "SGB vs physical gold returns taxes India comparison",
        "coaching vs self study UPSC success factors evidence", "online degree vs regular degree employer acceptance India",
        "NEET PG vs INI CET exam pattern comparison", "CUET vs state university entrance admission comparison",
        "government school vs private school learning outcomes Assam ASER", "Ayushman Bharat vs state insurance scheme coverage comparison Assam",
        "MGNREGA wage vs market wage Assam comparison study", "tea garden wage Assam vs West Bengal comparison",
        "Bharatmala vs PMGSY road outcomes comparison India", "metro vs BRTS cost per km Indian cities comparison",
        "coal power vs solar plus storage cost comparison India 2026", "hydrogen train vs diesel multiple unit cost Indian Railways",
        "NEWater recycling vs desalination cost Singapore comparison", "rice fortification vs dietary diversity anaemia evidence India",
        "direct benefit transfer leakage evidence LPG subsidy India", "PMFBY claim ratio state wise comparison study",
        "digital land records Assam vs Karnataka outcomes", "flood embankment vs wetland restoration Assam evidence comparison",
        "skill training placement rates Assam scheme comparison", "startup incubator Assam vs Bengaluru outcomes comparison",
    ],
    8: [
        "India quick commerce order value growth 2026 report", "Assam tea auction average price October 2026 Guwahati",
        "India two wheeler EV penetration October 2026 Vahan", "India passenger vehicle exports October 2026 SIAM",
        "India cement prices October 2026 dealer trend", "India steel consumption growth FY27 forecast",
        "India pharma US generics revenue 2026 outlook", "India IT fresher hiring 2026 campus offers trend",
        "India hotel occupancy RevPAR October 2026 HVS", "India aviation domestic passengers October 2026 DGCA",
        "India UPI person to merchant transactions October 2026", "India credit card spends festive season 2026 RBI",
        "India mutual fund equity inflows October 2026 AMFI", "India SIP stoppage ratio October 2026 trend",
        "India gold loan NBFC growth 2026 RBI data", "India microfinance portfolio quality October 2026 MFIN",
        "India life insurance APE October 2026 growth", "India general insurance motor premium growth 2026",
        "India smartphone festive sales 2026 Counterpoint", "India wearable shipments 2026 IDC report",
        "India PC shipments education segment 2026 IDC", "India data centre vacancy rate 2026 CBRE",
        "India flex office leasing Bengaluru 2026 report", "India logistics warehousing rents 2026 Knight Frank",
        "India quick service restaurant expansion 2026 store adds", "India multiplex box office October 2026 collections",
        "India OTT paid subscribers 2026 Ormax report", "India edtech funding 2026 Tracxn report",
        "India agritech startup funding 2026 report", "India EV charging operators revenue model 2026",
    ],
    9: [
        "Quelle est la capitale de la Mongolie ?", "¿Cuál es el río más largo de Asia?",
        "Wie heißt die Hauptstadt von Bhutan?", "Qual è la montagna più alta d'Italia?",
        "Qual é a capital do Sri Lanka?", "Wat is de hoofdstad van Nepal?",
        "Jaka jest stolica Mongolii?", "Jaké je hlavní město Bhútánu?",
        "Care este capitala Nepalului?", "Mekkora a Duna hossza?",
        "Hur många sjöar finns det i Finland?", "Hva er hovedstaden i Bhutan?",
        "Hvad er hovedstaden i Nepal?", "Mikä on Mongolian pääkaupunki?",
        "Скільки міст в Україні?", "Која је престоница Монголије?",
        "Колко области има България?", "რომელია მონღოლეთის დედაქალაქი?",
        "Որն է Բութանի մայրաքաղաքը?", "Azərbaycanın ən böyük gölü hansıdır?",
        "Қазақстанның ең үлкен көлі қайсы?", "Oʻzbekistonning poytaxti qayer?",
        "የኢትዮጵያ ዋና ከተማ ማን ናት?", "Waa maxay caasimadda Bhutan?",
        "Nini mji mkuu wa Mongolia?", "Kí ni olú-ìlú Nepal?",
        "Yini inhloko-dolobha yaseBhutan?", "မြန်မာနိုင်ငံရဲ့ အကြီးဆုံးမြစ် ဘာလဲ?",
        "ប្រទេសនេប៉ាល់មានខេត្តប៉ុន្មាន?", "ভুটানের রাজধানী কী?",
    ],
    10: [
        "renew Indian driving licence after moving to another state steps", "replace lost Aadhaar card and update mobile number together",
        "transfer two wheeler ownership within Assam same district process", "correct date of birth in CBSE certificate after passing class 10",
        "open zero balance account for MGNREGA wages Assam documents", "claim unclaimed PPF amount after account holder death",
        "close home loan and retrieve original property documents bank process", "port health insurance policy without losing waiting period India",
        "file consumer complaint for defective refrigerator in Assam", "get duplicate electricity bill and change name Assam APDCL",
        "apply for caste certificate for central government jobs in Assam", "obtain income certificate for scholarship in Assam online",
        "register marriage in Assam after religious ceremony documents", "apply for widow pension in Assam eligibility documents",
        "get legal heir certificate in Assam after father death", "transfer land mutation after inheritance in Assam process",
        "apply for building permission in Guwahati GMC documents fees", "obtain trade licence for cloud kitchen in Guwahati",
        "register partnership deed notarisation vs registration India difference", "start tea stall business licences Assam Guwahati checklist",
        "GST registration for online seller Assam threshold turnover", "file NIL GST return monthly steps GSTR-3B",
        "claim motor insurance for theft of bike Assam FIR timeline", "appeal rejected health insurance claim IRDAI ombudsman process",
        "transfer PF and pension EPS service history mismatch correction", "withdraw PF after 2 months unemployment advance claim process",
        "apply for duplicate PAN card after name change marriage", "correct father name in PAN and Aadhaar mismatch resolution",
        "open NPS account online and choose pension fund manager", "plan retirement corpus 1 crore by age 45 India calculation",
    ],
}


def main() -> None:
    rows = []
    for cat in range(1, 11):
        queries = QUERIES[cat]
        assert len(queries) == 30, (cat, len(queries))
        for i, q in enumerate(queries, start=1):
            qid = f"H-Q{(cat - 1) * 30 + i:03d}"
            rows.append({
                "query_id": qid, "category": cat, "category_name": CATS[cat], "query": q,
                "information_need": f"Answer the query as asked, for its stated place/time/subject: {q}",
                "evaluation_criteria": "Top-5 contains the specific answer/document (not only a hub); primary or reputable source preferred; correct jurisdiction/language; usable without further search.",
                "reference_sources": "Primary/official source for the subject where one exists; otherwise reputable secondary reporting.",
            })
    assert len(rows) == 300 and len({r["query"] for r in rows}) == 300
    out = HERE / "queries_holdout.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    # Extraction set: 100 synthetic-but-realistic pages, expected span marked.
    pages = []
    topics = [
        ("Ferry fare", "The one-way fare is Rs 40 per adult passenger."),
        ("Exam fee", "The application fee is Rs 1,200 for general candidates."),
        ("Helpline", "The toll-free helpline number is 1800-111-222."),
        ("Last date", "The last date to apply is 30 November 2026."),
        ("Eligibility age", "Applicants must be between 21 and 32 years old."),
        ("Office timing", "The office remains open from 10 AM to 5 PM on working days."),
        ("Capacity", "The reservoir has a gross storage capacity of 4.2 billion cubic metres."),
        ("Distance", "The total length of the bridge is 9.15 kilometres."),
        ("Population", "The district recorded a population of 891,275 in the census."),
        ("Fee waiver", "Candidates from economically weaker sections receive a full fee waiver."),
    ]
    for i in range(100):
        label, span = topics[i % len(topics)]
        pages.append({
            "page_id": f"H-P{i + 1:03d}", "topic": label,
            "html": f"<html><head><title>Notice {i + 1}</title></head><body><nav>Home | About</nav>"
                    f"<main><h1>Official notice {i + 1}</h1><p>This notice concerns {label.lower()}.</p>"
                    f"<p>{span}</p><p>Issued by the competent authority.</p></main>"
                    f"<footer>Copyright notice</footer></body></html>",
            "expected_span": span,
        })
    (HERE / "extraction_pages.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in pages), encoding="utf-8")
    print(f"wrote {out} sha256={digest} rows=300 extraction=100")


if __name__ == "__main__":
    main()
