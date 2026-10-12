#!/usr/bin/env python3
"""
Conference Matcher - Match remaining DBLP conferences to CORE rankings.

Uses:
  - Curated, verified mappings for known conference renamings, acronym variations,
    and historical conference keys (e.g., NIPS -> NeurIPS, KBSE -> ASE, SoCG -> COMPGEOM, etc.)
  - Case-insensitive acronym matching
  - Generates supplement/4-conf-matched.csv
"""

import csv
import re
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPLEMENT_DIR = REPO_ROOT / "conference-supplement"

DBLP_CSV = SUPPLEMENT_DIR / "2-dblp-unmatched.csv"
CORE_CSV = SUPPLEMENT_DIR / "3-source-pool.csv"
CORE_MASTER_CSV = REPO_ROOT / "conference-context" / "core-2026.csv"
OUT_CSV = SUPPLEMENT_DIR / "4-fuzzy-matched.csv"

# Curated, verified mappings for conferences in 2-dblp-unmatched.csv to 3-source-pool.csv
EXPLICIT_MAP = {
    # ─── A* Conferences ───
    'conf/mm': ('ACM Multimedia', 'HIGH', 'Official DBLP key for ACM Multimedia (ACMMM)'),
    'conf/nips': ('Advances in Neural Information Processing Systems (was NIPS)', 'HIGH', 'Conference renamed from NIPS to NeurIPS (2018)'),
    'conf/kbse': ('Automated Software Engineering Conference', 'HIGH', 'DBLP key for ASE (formerly Knowledge-Based Software Engineering KBSE)'),
    'conf/compgeom': ('International Symposium on Computational Geometry', 'HIGH', 'Official DBLP key for Symposium on Computational Geometry (SoCG)'),
    'conf/uss': ('Usenix Security Symposium', 'HIGH', 'Official DBLP key for USENIX Security Symposium'),
    'conf/sigsoft': ('ACM International Conference on the Foundations of Software Engineering (was ESEC/FSE, changed 2024; duplicate previously listed as ESEC, removed from DB)', 'HIGH', 'SIGSOFT is DBLP key for modern FSE papers (2018+), conf/fse is for older papers'),
    'conf/icmas': ('International Joint Conference on Autonomous Agents and Multiagent Systems (previously the International Conference on Multiagent Systems, ICMAS, changed in 2000)', 'HIGH', 'Historical DBLP key for ICMAS, joined with Agents and ATAL to form AAMAS in 2002'),

    # ─── A Conferences ───
    'conf/gis': ('ACM International Conference on Advances in Geographic Information Systems', 'HIGH', 'Official DBLP key for ACM SIGSPATIAL (GIS)'),
    'conf/ecml': ('European Conference on Machine Learning and Principles and Practice of Knowledge Discovery in Database (PKDD and ECML combined from 2008)', 'HIGH', 'ECML co-located and merged into ECML PKDD in 2008'),
    'conf/pkdd': ('European Conference on Machine Learning and Principles and Practice of Knowledge Discovery in Database (PKDD and ECML combined from 2008)', 'HIGH', 'PKDD co-located and merged into ECML PKDD in 2008'),
    'conf/csfw': ('IEEE Computer Security Foundations Symposium (was CSFW)', 'HIGH', 'Conference formerly CSFW (Computer Security Foundations Workshop)'),
    'conf/eurosp': ('IEEE European Symposium on Security and Privacy', 'HIGH', 'Official acronym EuroS&P on DBLP'),
    'conf/icmcs': ('IEEE International Conference on Multimedia and Expo', 'HIGH', 'Official DBLP key for IEEE International Conference on Multimedia and Expo (ICME), historically indexed under ICMCS'),
    'conf/iwpc': ('IEEE International Conference on Program Comprehension (previously IWPC, changed in 2006)', 'HIGH', 'Conference previously IWPC (changed to ICPC in 2006)'),
    'conf/icsm': ('IEEE International Conference on Software Maintenance and Evolution (prior to 2014 was ICSM, IEEE International Conference on Software Maintenance)', 'HIGH', 'Conference prior to 2014 was ICSM (changed to ICSME)'),
    'conf/ipps': ('IEEE International Parallel and Distributed Processing Symposium (was IPPS and SPDP)', 'HIGH', 'IPPS merged with SPDP to form IPDPS in 2000'),
    'conf/spdp': ('IEEE International Parallel and Distributed Processing Symposium (was IPPS and SPDP)', 'HIGH', 'SPDP merged with IPPS to form IPDPS in 2000'),
    'conf/visualization': ('IEEE Visualization', 'HIGH', 'Official DBLP key for IEEE VIS (IEEE Visualization)'),
    'conf/uml': ('International Conference on Model Driven Engineering Languages and Systems (Previously UML, changed in 2005)', 'HIGH', 'Historical DBLP key for UML conference, became MODELS in 2005'),
    'conf/wicsa': ('International Conference on Software Architecture (was previously WICSA)', 'HIGH', 'Historical DBLP key for WICSA, evolved into ICSA'),
    'conf/ah': ('International Conference on User Modelling, Adaptation, and Personalization (was AH and UM)', 'HIGH', 'Adaptive Hypermedia (AH) merged with UM to form UMAP in 2009'),
    'conf/um': ('International Conference on User Modelling, Adaptation, and Personalization (was AH and UM)', 'HIGH', 'User Modeling (UM) merged with AH to form UMAP in 2009'),
    'conf/wdag': ('International Symposium on Distributed Computing (was WDAG)', 'HIGH', 'Conference formerly WDAG (Workshop on Distributed Algorithms), renamed to DISC in 1998'),
    'conf/approx': ('International Workshop on Approximation Algorithms for Combinatorial Optimization Problems and International Conference on Randomization and Computation', 'HIGH', 'Co-located joint APPROX/RANDOM conference series'),
    'conf/random': ('International Workshop on Approximation Algorithms for Combinatorial Optimization Problems and International Conference on Randomization and Computation', 'HIGH', 'Co-located joint APPROX/RANDOM conference series'),
    'conf/pet': ('Privacy Enhancing Technologies Symposium (was International Workshop of Privacy Enhancing Technologies)', 'HIGH', 'Conference was International Workshop on Privacy Enhancing Technologies (PET), became PETS in 2008'),

    # ─── B Conferences ───
    'conf/lats': ('ACM Conference on Learning @ Scale', 'HIGH', 'Official DBLP key for ACM Learning @ Scale (L@S)'),
    'conf/wisec': ('ACM Conference on Security and Privacy in Wireless and Mobile Networks', 'HIGH', 'Official DBLP acronym WiSec for ACM WiSec'),
    'conf/lctrts': ('ACM SIGPLAN Conference on Languages, Compilers and Tools for Embedded Systems', 'HIGH', 'DBLP key for LCTES (formerly LCTRTS: Languages, Compilers, and Tools for Real-Time Systems)'),
    'conf/apgv': ('ACM Symposium on Applied Perception', 'HIGH', 'DBLP key for SAP (formerly APGV: Applied Perception in Graphics & Visualization)'),
    'conf/si3d': ('ACM-SIGRAPH Interactive 3D Graphics and Games', 'HIGH', 'Official DBLP key for Interactive 3D Graphics and Games (I3D / I3DG)'),
    'conf/wosp': ('ACM/SPEC International Conference on Performance Engineering', 'HIGH', 'Workshop on Software and Performance (WOSP) evolved into ICPE in 2010'),
    'conf/candc': ('Creativity and Cognition', 'HIGH', 'Official DBLP key for ACM Creativity and Cognition (C&C)'),
    'conf/ctrsa': ('Cryptographers Track at RSA Conference', 'HIGH', 'Official DBLP key for CT-RSA'),
    'conf/vissym': ('Eurographics/IEEE Symposium on Visualization', 'HIGH', 'Official DBLP key for EuroVis (formerly VisSym)'),
    'conf/ecmdafa': ('European Conference on Modelling Foundations and Applications', 'HIGH', 'DBLP key for ECMFA (formerly ECMDA-FA: Model-Driven Architecture Foundations and Applications)'),
    'conf/ectel': ('European Conference on Technology Enhanced Learning', 'HIGH', 'Official DBLP key for EC-TEL'),
    'conf/esws': ('Extended Semantic Web Conference (was European Semantic Web Conference)', 'HIGH', 'Conference was European Semantic Web Conference (ESWC/ESWS), renamed to Extended Semantic Web Conference'),
    'conf/ccnc': ('IEEE Consumer Communications and Networking Conference', 'HIGH', 'Official DBLP acronym CCNC for IEEE CCNC'),
    'conf/eScience': ('IEEE International Conference on e-Science and Grid Computing', 'HIGH', 'Official DBLP key for IEEE e-Science'),
    'conf/fuzzIEEE': ('IEEE International Conference on Fuzzy Systems', 'HIGH', 'Official DBLP key for FUZZ-IEEE'),
    'conf/scc': ('IEEE International Conference on Software Services Engineering (previously IEEE International Conference on Services Computing SCC)', 'HIGH', 'Conference was Services Computing (SCC), renamed to IEEE SSE in 2024'),
    'conf/IEEEscc': ('IEEE International Conference on Software Services Engineering (previously IEEE International Conference on Services Computing SCC)', 'HIGH', 'Conference was Services Computing (SCC), renamed to IEEE SSE in 2024'),
    'conf/apvis': ('IEEE Pacific Visualization Symposium (was APVIS)', 'HIGH', 'Historical DBLP key for APVIS, became PacificVis in 2008'),
    'conf/vl': ('IEEE Symposium on Visual Languages and Human-Centric Computing (was VL)', 'HIGH', 'Conference was Visual Languages (VL), expanded to VL/HCC in 2001'),
    'conf/asunam': ('IEEE/ACM International Conference on Advances in Social Networks Analysis and Mining', 'HIGH', 'Official DBLP key for ASONAM (indexed as ASUNAM on DBLP)'),
    'conf/sec': ('IFIP Information Security & Privacy Conference', 'HIGH', 'Official DBLP key for IFIP SEC (ICT Systems Security and Privacy Protection)'),
    'conf/ivs': ('Intelligent Vehicles Conference', 'HIGH', 'Official DBLP key for IEEE Intelligent Vehicles Symposium (IEEE-IV / IVS)'),
    'conf/automotiveUI': ('International ACM Conference on Automotive User Interfaces and Interactive Vehicular Applications', 'HIGH', 'Official DBLP key for AutomotiveUI'),
    'conf/evoapps': ('International Conference on Applications of Evolutionary Computation', 'HIGH', 'Official DBLP key for EvoApplications (EvoApps)'),
    'conf/mhci': ('International Conference on Human-Computer Interaction with Mobile Devices and Services', 'HIGH', 'Official DBLP key for MobileHCI (MHCI)'),
    'conf/mir': ('International Conference on Multimedia Retrieval (previously MIR)', 'HIGH', 'Conference was ACM Multimedia Information Retrieval (MIR), became ICMR in 2011'),
    'conf/icsp': ('International Conference on Software and System Processes (was ICSP prior to 2011)', 'HIGH', 'Conference was Software Process (ICSP) prior to 2011, became ICSSP'),
    'conf/apn': ('International Conference on the Application and Theory of Petri Nets and Concurrency', 'HIGH', 'Official DBLP key for Application and Theory of Petri Nets (APN)'),
    'conf/ercimdl': ('International Conference on Theory and Practice of Digital Libraries (was ECDL until 2010)', 'HIGH', 'DBLP key for TPDL (formerly ERCIM Digital Libraries / ECDL)'),
    'conf/icb': ('International Joint Conference on Biometrics', 'HIGH', 'International Conference on Biometrics (ICB) merged with BTAS to form IJCB'),
    'conf/btas': ('International Joint Conference on Biometrics', 'HIGH', 'Biometrics Theory, Applications and Systems (BTAS) merged with ICB to form IJCB'),
    'conf/rulemlrr': ('International Joint Conference on Rules and Reasoning', 'HIGH', 'Official DBLP key for RuleML+RR'),
    'conf/ruleml': ('International Joint Conference on Rules and Reasoning', 'HIGH', 'RuleML merged with RR to form RuleML+RR in 2017'),
    'conf/wea': ('International Symposium on Experimental Algorithms', 'HIGH', 'Conference was Workshop on Experimental Algorithms (WEA), became SEA'),
    'conf/fme': ('International Symposium on Formal Methods (was Formal Methods Europe FME)', 'HIGH', 'Historical DBLP key for Formal Methods Europe (FME), became FM'),
    'conf/iwpec': ('International Symposium on Parameterized and Exact Computation (was IWPEC pre 2004)', 'HIGH', 'Historical DBLP key for IWPEC, became IPEC in 2010'),
    'conf/ssd': ('International Symposium on Spatial and Temporal Databases', 'HIGH', 'Conference was Symposium on Spatial Databases (SSD), became SSTD in 2001'),
    'conf/kcap': ('Knowledge capture', 'HIGH', 'Official DBLP key for K-CAP'),

    # ─── C Conferences ───
    'conf/dimea': ('ACM International Conference on Advances in Computer Entertainment (merged with DIMEA, Digital Interactive Media in Entertainment and Arts, in 2009)', 'HIGH', 'Historical DBLP key for DIMEA, merged with ACE in 2009'),
    'conf/tvx': ('ACM International Conference on Interactive Media Experiences', 'HIGH', 'Conference was TVX (Interactive Experiences for TV & Video), renamed to IMX in 2020'),
    'conf/mmasia': ('ACM International Conference on Multimedia in Asia', 'HIGH', 'Official DBLP key for ACM MM Asia'),
    'conf/rbac': ('ACM Symposium on Access Control Models and Technologies (previously ACM Workshop on Role-Based Access Control, RBAC, changed in 2000)', 'HIGH', 'Historical DBLP key for RBAC workshop, became SACMAT in 2001'),
    'conf/ivapp': ('Applications of Information Visualization', 'HIGH', 'Official DBLP key for IVAPP'),
    'conf/cig': ('Conference on Games (was Computational Intelligence and Games CIG)', 'HIGH', 'Conference was Computational Intelligence and Games (CIG), renamed to CoG in 2019'),
    'conf/mkm': ('Conference on Intelligent Computer Mathematics', 'HIGH', 'Mathematical Knowledge Management (MKM) co-founded and merged into CICM in 2008'),
    'conf/csee': ('Conference on Software Engineering Education and Training (previously Conference is Software Engineering Education, CSEE, changed in 1997)', 'HIGH', 'Conference was CSEE, renamed to CSEET in 1997'),
    'conf/eurovr': ('Euro XR International Conference (was Euro VR)', 'HIGH', 'Conference was EuroVR, rebranded to EuroXR in 2021'),
    'conf/eceg': ('European Conference on Digital Government (was European Conference on e-Government ECEG)', 'HIGH', 'Conference was ECEG, renamed to ECDG in 2019'),
    'conf/pvm': ('European MPI Users\' Group Conference', 'HIGH', 'EuroMPI originated from European PVM/MPI Users\' Group (PVM on DBLP)'),
    'conf/ewcg': ('European Workshop on Computational Geometry', 'HIGH', 'Official DBLP key for EuroCG (EWCG: European Workshop on Computational Geometry)'),
    'conf/is': ('IEEE International Conference on Intelligent Systems', 'HIGH', 'Official DBLP key for IEEE International Conference on Intelligent Systems (IEEE IS)'),
    'conf/adprl': ('IEEE International Symposium on Adaptive Dynamic Programming and Reinforcement Learning', 'HIGH', 'Official DBLP key for IEEE ADPRL'),
    'conf/ieeealife': ('IEEE International Symposium on Artificial Life', 'HIGH', 'Official DBLP key for IEEE ALife'),
    'conf/sis': ('IEEE Swarm Intelligence Symposium', 'HIGH', 'Official DBLP key for IEEE SIS'),
    'conf/cifer': ('IEEE Symposium on Computational Intelligence for Financial Engineering', 'HIGH', 'Official DBLP key for IEEE CIFEr'),
    'conf/cimsivp': ('IEEE Symposium on Computational intelligence for Multimedia Signal and Vision Processing', 'HIGH', 'Official DBLP key for IEEE CIMSIVP'),
    'conf/cisda': ('IEEE Symposium on Computational Intelligence for Security and Defence Applications', 'HIGH', 'Official DBLP key for IEEE CISDA'),
    'conf/cica': ('IEEE Symposium on Computational Intelligence in Control and Automation', 'HIGH', 'Official DBLP key for IEEE CICA'),
    'conf/cics': ('IEEE Symposium on Computational Intelligence in Cyber Security', 'HIGH', 'Official DBLP key for IEEE CICS'),
    'conf/bdc': ('IEEE/ACM International Conference on Big Data Computing, Applications and Technologies', 'HIGH', 'Official DBLP key for BDCAT (BDC on DBLP)'),
    'conf/vlsi': ('IEEE/IFIP International Conference on Very Large Scale Integration of System-on-Chip', 'HIGH', 'Official DBLP key for VLSI-SoC (VLSI on DBLP)'),
    'conf/ifip5-5': ('IFIP Working Conferences on Virtual Enterprises', 'HIGH', 'Official DBLP key for PRO-VE (IFIP Working Group 5.5)'),
    'conf/ima': ('IMA International Conference on Cryptography and Coding', 'HIGH', 'Official DBLP key for IMACC (IMA on DBLP)'),
    'conf/ih': ('Information Hiding and Multimedia Security Workshop', 'HIGH', 'Information Hiding (IH) merged into IH&MMSec in 2013'),
    'conf/mmsec': ('Information Hiding and Multimedia Security Workshop', 'HIGH', 'Multimedia and Security (MM&Sec) merged into IH&MMSec in 2013'),
    'conf/isw': ('Information Security Conference', 'HIGH', 'Conference was Information Security Workshop (ISW), became ISC in 2001'),
    'conf/asm': ('International Conference Abstract State Machines, Alloy, B, TLA, VDM, and Z (Previously International Conference of B and Z Users, ZB, changed in 2008)', 'HIGH', 'Abstract State Machines (ASM) joined to form ABZ conference series in 2008'),
    'conf/hpcasia': ('International Conference and Exhibition on High Performance Computing in the Asia-Pacific Region', 'HIGH', 'Official DBLP key for HPC Asia'),
    'conf/colcom': ('International Conference on Collaborative Computing: Networks, Applications and Worksharing', 'HIGH', 'Official DBLP key for CollaborateCom (ColCom on DBLP)'),
    'conf/wia': ('International Conference on Implementation and Application of Automata', 'HIGH', 'Workshop on Implementation of Automata (WIA) became CIAA in 2000'),
    'conf/ieaaie': ('International Conference on Industrial and Engineering Applications of Artificial Intelligence and Expert Systems', 'HIGH', 'Official DBLP key for IEA/AIE'),
    'conf/iics': ('International Conference on Innovations for Community Services (was Innovative Internet Computer Systems IICS until 2014)', 'HIGH', 'Historical DBLP key for IICS, renamed to I4CS in 2015'),
    'conf/pppj': ('International Conference on Managed Programming Languages and Runtimes (was ManLang and previously Principles and Practice of Programming in Java: PPPJ)', 'HIGH', 'Conference was PPPJ, renamed to ManLang, now MPLR'),
    'conf/RelMiCS': ('International Conference on Relational and AlgebraicMethods in Computer Science (was International Conference on Relational Methods in Computer Science RelMiCS)', 'HIGH', 'Conference was RelMiCS, renamed to RAMiCS in 2011'),
    'conf/pts': ('International Conference on Testing Software and Systems', 'HIGH', 'Protocol Test Systems (PTS) / TestCom evolved into ICTSS in 2010'),
    'conf/cdmake': ('International Cross-Domain Conference for Machine Learning and Knowledge Extraction', 'HIGH', 'Official DBLP key for CD-MAKE'),
    'conf/rsctc': ('International Joint Conference on Rough Sets (2014 International Conference on Rough Sets and Current Trends in Computing joined with 3 others)', 'HIGH', 'Rough Sets and Current Trends in Computing (RSCTC) merged into IJCRS in 2014'),
    'conf/ijcrs': ('International Joint Conference on Rough Sets (2014 International Conference on Rough Sets and Current Trends in Computing joined with 3 others)', 'HIGH', 'Official DBLP key for IJCRS'),
    'conf/kesamsta': ('International KES Conference on Agents and Multiagent systems - Technologies and Applications', 'HIGH', 'Official DBLP key for KES AMSTA'),
    'conf/dsrt': ('International Symposium on Distributed Simulation and Real Time Applications', 'HIGH', 'Official DBLP key for DS-RT'),
    'conf/wikis': ('International Symposium on Open Collaboration (was International Symposiums on Wikis)', 'HIGH', 'Conference was International Symposium on Wikis (WikiSym), became OpenSym in 2014'),
    'conf/coin-ws': ('International Workshop on Coordination, Organizations, Institutions, Norms and Ethics for Governance of Multi-Agent Systems (Ethics added 2020)', 'HIGH', 'Official DBLP key for COINE (formerly COIN, International Workshop on Coordination, Organizations, Institutions, Norms and Ethics for Governance of Multi-Agent Systems)'),
    'conf/wordnet': ('International Wordnet Conference (Global Wordnet Conference)', 'HIGH', 'Official DBLP key for Global Wordnet Conference (GWC)'),
    'conf/iwsm': ('Joint Conference of the International Workshop on Software Measurement and the International Conference on Software Process and Product Measurement (IWSM and Mensura combined from 2007)', 'HIGH', 'Official DBLP key for IWSM / Mensura joint conference'),
    'conf/kesidt': ('KES International Symposium on Intelligent Decision Technologies', 'HIGH', 'Official DBLP key for KES IDT'),
    'conf/mtsummit': ('Machine Translation Summit', 'HIGH', 'Official DBLP key for MT Summit'),
    'conf/qsic': ('Software Quality, Reliability, and Security (was International Conference on Quality Software, QSIC, that merged with SERE 2015)', 'HIGH', 'Historical DBLP key for QSIC, merged into QRS in 2015'),
    'conf/hotchips': ('Symposium on High Performance Chips', 'HIGH', 'Official DBLP key for Hot Chips (HOTCHIPS)'),
    'conf/eurova-ws': ('Visual Analytics', 'HIGH', 'Official DBLP key for EuroVA (International EuroVis Workshop on Visual Analytics)'),
}


def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def clean_str(s: str) -> str:
    s = strip_accents(s).lower()
    s = re.sub(r'[^\w\s]', ' ', s)
    return ' '.join(s.split())


def build_matcher():
    with open(DBLP_CSV, encoding='utf-8') as f:
        dblp_rows = list(csv.DictReader(f))
    with open(CORE_CSV, encoding='utf-8') as f:
        core_rows = list(csv.DictReader(f))

    core_master_rows = []
    if CORE_MASTER_CSV.exists():
        with open(CORE_MASTER_CSV, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                ranking = r.get('Ranking', '').strip()
                if ranking in {'A*', 'A', 'B', 'C'}:
                    core_master_rows.append({
                        'title': r['Title'].strip(),
                        'acronym': r['Acronym'].strip(),
                        'ranking': ranking,
                    })

    core_master_by_clean = {clean_str(r['title']): r for r in core_master_rows}
    core_by_clean = {clean_str(r['title']): r for r in core_rows}
    core_by_acronym = {r['acronym'].lower(): r for r in core_rows}

    matched_dblp = {}
    used_core_titles = set()

    # Pass 1: Explicit curated mappings (verified against DBLP bibliography and history)
    for d in dblp_rows:
        key = d['dblp_key']
        if key in EXPLICIT_MAP:
            target_title, conf, reason = EXPLICIT_MAP[key]
            cleaned_target = clean_str(target_title)
            if cleaned_target in core_master_by_clean:
                c = core_master_by_clean[cleaned_target]
                matched_dblp[key] = (c, conf, reason)
                used_core_titles.add(c['title'])
            else:
                for c in core_rows:
                    if clean_str(c['title']) == cleaned_target:
                        matched_dblp[key] = (c, conf, reason)
                        used_core_titles.add(c['title'])
                        break

    # Pass 2: Case-insensitive acronym match for any remaining entries
    for d in dblp_rows:
        key = d['dblp_key']
        if key in matched_dblp:
            continue

        dblp_acronym_lower = d['dblp_acronym'].lower()
        if dblp_acronym_lower in core_by_acronym:
            c = core_by_acronym[dblp_acronym_lower]
            if c['title'] not in used_core_titles:
                matched_dblp[key] = (c, 'HIGH', f"Acronym match: {d['dblp_acronym']}")
                used_core_titles.add(c['title'])

    # Prepare output rows
    output_rows = []
    high_count = 0
    none_count = 0

    for d in dblp_rows:
        key = d['dblp_key']
        acronym = d['dblp_acronym']

        if key in matched_dblp:
            c, conf, reason = matched_dblp[key]
            high_count += 1
            output_rows.append({
                'dblp_key': key,
                'dblp_acronym': acronym,
                'core_title': c['title'],
                'acronym': c['acronym'],
                'ranking': c['ranking'],
                'confidence': conf,
                'reasoning': reason,
            })
        else:
            none_count += 1
            output_rows.append({
                'dblp_key': key,
                'dblp_acronym': acronym,
                'core_title': 'NO_MATCH',
                'acronym': '',
                'ranking': '',
                'confidence': 'NONE',
                'reasoning': 'Not in CORE rankings (regional workshop, defunct, or unranked venue)',
            })

    # Write output
    fieldnames = [
        'dblp_key', 'dblp_acronym', 'core_title', 'acronym', 'ranking',
        'confidence', 'reasoning'
    ]
    with open(OUT_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Generated {len(output_rows)} total rows in {OUT_CSV}")
    print(f"  HIGH confidence matches:   {high_count}")
    print(f"  NONE (NO_MATCH):           {none_count}")
    print(f"  Total matched conferences: {high_count}")
    print(f"  Distinct CORE matched:     {len(used_core_titles)}")


if __name__ == '__main__':
    build_matcher()
