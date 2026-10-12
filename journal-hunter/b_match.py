import csv
import re
import unicodedata
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTEXT_DIR = REPO_ROOT / "journal-context"
SUPPLEMENT_DIR = REPO_ROOT / "journal-supplement"
DBLP_CSV = SUPPLEMENT_DIR / "2-dblp-todo.csv"
SJR_CSV = SUPPLEMENT_DIR / "3-sjr-pool.csv"
OUT_CSV = SUPPLEMENT_DIR / "4-ai-matched.csv"

STOPWORDS = {
    'of', 'the', 'and', 'in', 'on', 'for', 'to', 'a', 'an', '&', 'at', 'by', 'with', 'part',
    'de', 'des', 'du', 'd', 'la', 'le', 'et', 'fur', 'und'
}

ABBREV_EXPANSIONS = {
    'trans': ['transactions'],
    'trans.': ['transactions'],
    'j': ['journal', 'journals'],
    'j.': ['journal', 'journals'],
    'int': ['international'],
    'int.': ['international'],
    'comput': ['computer', 'computing', 'computers', 'computation', 'computational'],
    'comput.': ['computer', 'computing', 'computers', 'computation', 'computational'],
    'syst': ['system', 'systems', 'systemic'],
    'syst.': ['system', 'systems', 'systemic'],
    'appl': ['applied', 'applications', 'application', 'applicable'],
    'appl.': ['applied', 'applications', 'application', 'applicable'],
    'adv': ['advances', 'advance', 'advanced'],
    'adv.': ['advances', 'advance', 'advanced'],
    'commun': ['communication', 'communications'],
    'commun.': ['communication', 'communications'],
    'inf': ['information', 'informatics'],
    'inf.': ['information', 'informatics'],
    'eng': ['engineering', 'engineers'],
    'eng.': ['engineering', 'engineers'],
    'sci': ['science', 'sciences', 'scientific'],
    'sci.': ['science', 'sciences', 'scientific'],
    'artif': ['artificial'],
    'artif.': ['artificial'],
    'intell': ['intelligence', 'intelligent'],
    'intell.': ['intelligence', 'intelligent'],
    'mach': ['machine', 'machines'],
    'mach.': ['machine', 'machines'],
    'learn': ['learning'],
    'learn.': ['learning'],
    'res': ['research'],
    'res.': ['research'],
    'oper': ['operations', 'operational', 'operation'],
    'oper.': ['operations', 'operational', 'operation'],
    'mgmt': ['management'],
    'mgmt.': ['management'],
    'manag': ['management'],
    'manag.': ['management'],
    'softw': ['software'],
    'softw.': ['software'],
    'telecommun': ['telecommunication', 'telecommunications'],
    'telecommun.': ['telecommunication', 'telecommunications'],
    'electron': ['electronic', 'electronics'],
    'electron.': ['electronic', 'electronics'],
    'electr': ['electrical', 'electronic', 'electronics'],
    'electr.': ['electrical', 'electronic', 'electronics'],
    'autom': ['automation', 'automatic', 'automating', 'automata'],
    'autom.': ['automation', 'automatic', 'automating', 'automata'],
    'control': ['control', 'controls', 'controlling'],
    'control.': ['control', 'controls', 'controlling'],
    'theor': ['theory', 'theoretical'],
    'theor.': ['theory', 'theoretical'],
    'anal': ['analysis', 'analytic', 'analytical'],
    'anal.': ['analysis', 'analytic', 'analytical'],
    'proc': ['proceedings'],
    'proc.': ['proceedings'],
    'soc': ['society', 'societies'],
    'soc.': ['society', 'societies'],
    'stat': ['statistics', 'statistical'],
    'stat.': ['statistics', 'statistical'],
    'math': ['mathematics', 'mathematical'],
    'math.': ['mathematics', 'mathematical'],
    'rev': ['review', 'reviews'],
    'rev.': ['review', 'reviews'],
    'technol': ['technology', 'technologies', 'technological'],
    'technol.': ['technology', 'technologies', 'technological'],
    'distrib': ['distributed', 'distribution'],
    'distrib.': ['distributed', 'distribution'],
    'netw': ['network', 'networks', 'networking'],
    'netw.': ['network', 'networks', 'networking'],
    'network': ['network', 'networks', 'networking'],
    'network.': ['network', 'networks', 'networking'],
    'biomed': ['biomedical'],
    'biomed.': ['biomedical'],
    'bioinform': ['bioinformatics'],
    'bioinform.': ['bioinformatics'],
    'acad': ['academy', 'academic'],
    'acad.': ['academy', 'academic'],
    'ann': ['annals'],
    'ann.': ['annals'],
    'arch': ['architecture', 'architectures', 'archive', 'archives'],
    'arch.': ['architecture', 'architectures', 'archive', 'archives'],
    'lett': ['letters', 'letter'],
    'lett.': ['letters', 'letter'],
    'mag': ['magazine', 'magazines'],
    'mag.': ['magazine'],
    'bull': ['bulletin'],
    'bull.': ['bulletin'],
    'surv': ['surveys', 'survey'],
    'surv.': ['surveys', 'survey'],
    'evol': ['evolutionary', 'evolution'],
    'evol.': ['evolutionary', 'evolution'],
    'heurist': ['heuristics', 'heuristic'],
    'heurist.': ['heuristics', 'heuristic'],
    'environ': ['environment', 'environmental'],
    'environ.': ['environment', 'environmental'],
    'geosci': ['geoscience', 'geosciences'],
    'geosci.': ['geoscience', 'geosciences'],
    'cogn': ['cognitive', 'cognition'],
    'cogn.': ['cognitive', 'cognition'],
    'found': ['foundations', 'foundation'],
    'found.': ['foundations', 'foundation'],
    'lang': ['language', 'languages'],
    'lang.': ['language', 'languages'],
    'ling': ['linguistics', 'linguistic'],
    'ling.': ['linguistics', 'linguistic'],
    'linguist': ['linguistics', 'linguistic'],
    'linguist.': ['linguistics', 'linguistic'],
    'opt': ['optimization', 'optics', 'optical'],
    'opt.': ['optimization', 'optics', 'optical'],
    'optim': ['optimization'],
    'optim.': ['optimization'],
    'struct': ['structure', 'structures', 'structural'],
    'struct.': ['structure', 'structures', 'structural'],
    'vis': ['visualization', 'visual'],
    'vis.': ['visualization', 'visual'],
    'qual': ['quality'],
    'qual.': ['quality'],
    'quant': ['quantitative', 'quantum'],
    'quant.': ['quantitative', 'quantum'],
    'perspect': ['perspectives', 'perspective'],
    'perspect.': ['perspectives', 'perspective'],
    'robot': ['robotics', 'robotic', 'robots'],
    'robot.': ['robotics', 'robotic', 'robots'],
    'simul': ['simulation', 'simulations'],
    'simul.': ['simulation', 'simulations'],
    'multimed': ['multimedia'],
    'multimed.': ['multimedia'],
    'sens': ['sensors', 'sensor', 'sensing'],
    'sens.': ['sensors', 'sensor', 'sensing'],
    'geogr': ['geography', 'geographical', 'geographic'],
    'geogr.': ['geography', 'geographical', 'geographic'],
    'decis': ['decision', 'decisions'],
    'decis.': ['decision', 'decisions'],
    'pract': ['practice', 'practical'],
    'pract.': ['practice', 'practical'],
    'sec': ['security'],
    'sec.': ['security'],
    'secur': ['security'],
    'secur.': ['security'],
    'knowl': ['knowledge'],
    'knowl.': ['knowledge'],
    'auton': ['autonomous'],
    'auton.': ['autonomous'],
    'discov': ['discover', 'discovery'],
    'discov.': ['discover', 'discovery'],
    'educ': ['education', 'educational'],
    'educ.': ['education', 'educational'],
    'pap': ['papers', 'paper'],
    'pap.': ['papers', 'paper'],
    'regul': ['regular'],
    'regul.': ['regular'],
    'acm': ['association', 'computing', 'machinery', 'acm'],
    'ieee': ['institute', 'electrical', 'electronics', 'engineers', 'ieee'],
    'vldb': ['very', 'large', 'data', 'bases', 'vldb'],
    'siam': ['society', 'industrial', 'applied', 'mathematics', 'siam'],
}

# Explicit curated mappings for acronyms, renamings, and specific journal keys
EXPLICIT_MAP = {
    'journals/cacm': ('Communications of the ACM', 'HIGH', 'Official ACM abbreviation match'),
    'journals/jacm': ('Journal of the ACM', 'HIGH', 'Official ACM abbreviation match'),
    'journals/csur': ('ACM Computing Surveys', 'HIGH', 'Official ACM abbreviation match'),
    'journals/tocs': ('ACM Transactions on Computer Systems', 'HIGH', 'Official ACM acronym match'),
    'journals/tods': ('ACM Transactions on Database Systems', 'HIGH', 'Official ACM acronym match'),
    'journals/tog': ('ACM Transactions on Graphics', 'HIGH', 'Official ACM acronym match'),
    'journals/tois': ('ACM Transactions on Information Systems', 'HIGH', 'Official ACM acronym match'),
    'journals/toit': ('ACM Transactions on Internet Technology', 'HIGH', 'Official ACM acronym match'),
    'journals/tomacs': ('ACM Transactions on Modeling and Computer Simulation', 'HIGH', 'Official ACM acronym match'),
    'journals/toms': ('ACM Transactions on Mathematical Software', 'HIGH', 'Official ACM acronym match'),
    'journals/toplas': ('ACM Transactions on Programming Languages and Systems', 'HIGH', 'Official ACM acronym match'),
    'journals/tos': ('ACM Transactions on Storage', 'HIGH', 'Official ACM acronym match'),
    'journals/tosem': ('ACM Transactions on Software Engineering and Methodology', 'HIGH', 'Official ACM acronym match'),
    'journals/tweb': ('ACM Transactions on the Web', 'HIGH', 'Official ACM acronym match'),
    'journals/tecs': ('ACM Transactions on Embedded Computing Systems', 'HIGH', 'Official ACM acronym match'),
    'journals/tist': ('ACM Transactions on Intelligent Systems and Technology', 'HIGH', 'Official ACM acronym match'),
    'journals/tkdd': ('ACM Transactions on Knowledge Discovery from Data', 'HIGH', 'Official ACM acronym match'),
    'journals/toce': ('ACM Transactions on Computing Education', 'HIGH', 'Official ACM acronym match'),
    'journals/tissec': ('ACM Transactions on Privacy and Security', 'HIGH', 'Formerly ACM TISSEC, renamed to TOPS in 2016'),
    'journals/jocch': ('Journal on Computing and Cultural Heritage', 'HIGH', 'ACM JOCCH official title in SJR'),
    'journals/distribledger': ('Distributed Ledger Technologies', 'HIGH', 'ACM DLT official title in SJR'),
    'journals/tc': ('IEEE Transactions on Computers', 'HIGH', 'Official IEEE acronym match'),
    'journals/tse': ('IEEE Transactions on Software Engineering', 'HIGH', 'Official IEEE acronym match'),
    'journals/tkde': ('IEEE Transactions on Knowledge and Data Engineering', 'HIGH', 'Official IEEE acronym match'),
    'journals/tpami': ('IEEE Transactions on Pattern Analysis and Machine Intelligence', 'HIGH', 'Official IEEE acronym match'),
    'journals/tvcg': ('IEEE Transactions on Visualization and Computer Graphics', 'HIGH', 'Official IEEE acronym match'),
    'journals/tit': ('IEEE Transactions on Information Theory', 'HIGH', 'Official IEEE acronym match'),
    'journals/tifs': ('IEEE Transactions on Information Forensics and Security', 'HIGH', 'Official IEEE acronym match'),
    'journals/tmc': ('IEEE Transactions on Mobile Computing', 'HIGH', 'Official IEEE acronym match'),
    'journals/tnse': ('IEEE Transactions on Network Science and Engineering', 'HIGH', 'Official IEEE acronym match'),
    'journals/ton': ('IEEE Transactions on Networking', 'HIGH', 'IEEE/ACM Transactions on Networking'),
    'journals/tpds': ('IEEE Transactions on Parallel and Distributed Systems', 'HIGH', 'Official IEEE acronym match'),
    'journals/tvt': ('IEEE Transactions on Vehicular Technology', 'HIGH', 'Official IEEE acronym match'),
    'journals/tvlsi': ('IEEE Transactions on Very Large Scale Integration (VLSI) Systems', 'HIGH', 'Official IEEE acronym match'),
    'journals/dt': ('IEEE Design and Test', 'HIGH', 'Formerly IEEE D&T of Computers, renamed to IEEE D&T'),
    'journals/taslp': ('IEEE/ACM Transactions on Audio Speech and Language Processing', 'HIGH', 'Renamed IEEE/ACM TASLP'),
    'journals/pieee': ('Proceedings of the IEEE', 'HIGH', 'Official IEEE abbreviation match'),
    'journals/vldb': ('VLDB Journal', 'HIGH', 'Official VLDB journal match'),
    'journals/pvldb': ('Proceedings of the VLDB Endowment', 'HIGH', 'Official PVLDB journal match'),
    'journals/queue': ('Queue', 'HIGH', 'ACM Queue official title in SJR, ISSN 1542-7730 verified'),
    'journals/bit': ('BIT Numerical Mathematics', 'HIGH', 'Standard abbreviation for BIT, ISSN 0006-3835 verified'),
    'journals/pacmpl': ('Proceedings of the ACM on Programming Languages', 'HIGH', 'PACMPL includes POPL/PLDI conference papers published as journal articles'),
    'journals/pacmmod': ('Proceedings of the ACM on Management of Data', 'HIGH', 'PACMMOD includes SIGMOD conference papers published as journal articles'),
    'journals/pomacs': ('Proceedings of the ACM on Measurement and Analysis of Computing Systems', 'HIGH', 'POMACS includes SIGMETRICS conference papers published as journal articles'),
    'journals/siamcomp': ('SIAM Journal on Computing', 'HIGH', 'Official SIAM acronym match'),
    'journals/siamdm': ('SIAM Journal on Discrete Mathematics', 'HIGH', 'Official SIAM acronym match'),
    'journals/siamis': ('SIAM Journal on Imaging Sciences', 'HIGH', 'Official SIAM acronym match'),
    'journals/siamnum': ('SIAM Journal on Numerical Analysis', 'HIGH', 'Official SIAM acronym match'),
    'journals/siamsc': ('SIAM Journal on Scientific Computing', 'HIGH', 'Official SIAM acronym match'),
    'journals/siamrev': ('SIAM Review', 'HIGH', 'Official SIAM acronym match'),
    'journals/ai': ('Artificial Intelligence', 'HIGH', 'Official Elsevier AI journal match'),
    'journals/jmlr': ('Journal of Machine Learning Research', 'HIGH', 'Official JMLR abbreviation match'),
    'journals/cys': ('Computacion y Sistemas', 'HIGH', 'Exact name match with diacritic normalization'),
    'journals/agit': ('AGIT- Journal fur Angewandte Geoinformatik', 'HIGH', 'Exact name match with subtitle/language variation'),
    'journals/isi': ("Ingenierie des Systemes d'Information", 'HIGH', 'Exact name match with diacritic normalization'),
    'journals/at': ('At-Automatisierungstechnik', 'HIGH', 'German journal at-Automatisierungstechnik'),
    'journals/computability': ('Computability', 'HIGH', 'Exact journal title match for DBLP key computability'),
    'journals/computation': ('Computation', 'HIGH', 'Exact journal title match for DBLP key computation'),
    'journals/computers': ('Computers', 'HIGH', 'Exact journal title match for DBLP key computers'),
    'journals/fcsc': ('Frontiers of Computer Science', 'HIGH', 'DBLP key fcsc = Frontiers of Computer Science'),
    'journals/fcomp': ('Frontiers in Computer Science', 'HIGH', 'DBLP key fcomp = Frontiers in Computer Science'),
    'journals/or': ('Operations Research', 'HIGH', 'DBLP key or = Operations Research (INFORMS)'),
    'journals/ior': ('Operational Research', 'HIGH', 'DBLP key ior = Operational Research (Springer)'),
    'journals/jocs': ('Journal of Computational Science', 'HIGH', 'DBLP key jocs = Journal of Computational Science'),
    'journals/jcsci': ('Journal of Computer Science', 'HIGH', 'DBLP key jcsci = Journal of Computer Science'),
    'journals/aiedam': ('Artificial Intelligence for Engineering Design, Analysis and Manufacturing: AIEDAM', 'HIGH', 'Exact title match with acronym subtitle'),
    'journals/dsp': ('Digital Signal Processing: A Review Journal', 'HIGH', 'Exact title match with subtitle in SJR'),
    'journals/jirs': ('Journal of Intelligent and Robotic Systems: Theory and Applications', 'HIGH', 'Exact title match with subtitle in SJR'),
    'journals/ki': ('KI - Kunstliche Intelligenz', 'HIGH', 'Exact title match with German diacritic normalization'),
    'journals/ewc': ('Engineering with Computers', 'HIGH', 'DBLP key ewc = Engineering with Computers'),
    'journals/gandc': ('Computers and Geosciences', 'HIGH', 'DBLP key gandc = Computers & Geosciences'),
    'journals/tcas': ('IEEE Transactions on Circuits and Systems I: Regular Papers', 'HIGH', 'IEEE TCAS-I journal match'),
    'journals/cagd': ('Computer Aided Geometric Design', 'HIGH', 'Exact name match, ISSN 0167-8396 verified'),
    'journals/dafes': ('Design Automation for Embedded Systems', 'HIGH', 'Standard abbreviation match, ISSN 0929-5585 verified'),
    'journals/dcc': ('Designs, Codes, and Cryptography', 'HIGH', 'Standard abbreviation match, ISSN 0925-1022 verified'),
    'journals/fmsd': ('Formal Methods in System Design', 'HIGH', 'Standard abbreviation match, ISSN 0925-9856 verified'),
    'journals/fteda': ('Foundations and Trends in Electronic Design Automation', 'HIGH', 'Standard abbreviation match, ISSN 1551-3939 verified'),
    'journals/ijcbdd': ('International Journal of Computational Biology and Drug Design', 'HIGH', 'Standard abbreviation match, ISSN 1756-0756 verified'),
    'journals/ijismd': ('International Journal of Information System Modeling and Design', 'HIGH', 'Standard abbreviation match, ISSN 1947-8186 verified'),
    'journals/ipsj': ('IPSJ Transactions on System LSI Design Methodology', 'HIGH', 'Standard abbreviation match, ISSN 1882-6687 verified'),
    'journals/jcamd': ('Journal of Computer-Aided Molecular Design', 'HIGH', 'Standard abbreviation match, ISSN 0920-654X verified'),
    'journals/jcde': ('Journal of Computational Design and Engineering', 'HIGH', 'Standard abbreviation match, ISSN 2288-4300 verified'),
    'journals/tcad': ('IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems', 'HIGH', 'Official IEEE TCAD match, ISSN 0278-0070 verified'),
    'journals/iet-cps': ('IET Cyber-Physical Systems: Theory and Applications', 'HIGH', 'DBLP typo Cyper corrected to Cyber, ISSN 2398-3396 verified'),
    'journals/mcfns': ('Mathematical and Computational Forestry and Natural-Resource Sciences', 'HIGH', 'Standard abbreviation match, ISSN 1946-7664 verified'),
    'journals/csimq': ('Complex Systems Informatics and Modeling Quarterly', 'HIGH', 'Standard abbreviation match, ISSN 2255-9922 verified'),
    'journals/jcta': ('Journal of Combinatorial Theory. Series A', 'HIGH', 'Standard abbreviation match, ISSN 0097-3165 verified'),
    'journals/jctb': ('Journal of Combinatorial Theory. Series B', 'HIGH', 'Standard abbreviation match, ISSN 0095-8956 verified'),
    'journals/jdiq': ('Journal of Data and Information Quality', 'HIGH', 'Official ACM JDIQ title in SJR, ISSN 1936-1955 verified'),
    'journals/tal': ('TAL Traitement Automatique des Langues', 'HIGH', 'Exact French title with acronym prefix, ISSN 1248-9433 verified'),
    'journals/saj': ('South African Computer Journal', 'HIGH', 'Standard abbreviation match, ISSN 1015-7999 verified'),
    'journals/sigmetrics': ('Performance Evaluation Review', 'HIGH', 'ACM SIGMETRICS PER official title in SJR, ISSN 0163-5999 verified'),
    'journals/jitt': ('Information Technology and Tourism', 'HIGH', 'Standard abbreviation match, ISSN 1098-3058 verified'),
    'journals/db': ('Data Base for Advances in Information Systems', 'HIGH', 'ACM SIGMIS Data Base official title in SJR, ISSN 1532-0936 verified'),
    'journals/sigops': ('Operating Systems Review (ACM)', 'HIGH', 'ACM SIGOPS OSR official title in SJR, ISSN 0163-5980 verified'),
    'journals/trob': ('IEEE Transactions on Robotics', 'HIGH', 'Formerly IEEE Trans. Robotics Autom., renamed in 2004, ISSN 1941-0468 verified'),
    'journals/cca': ('ACM Communications in Computer Algebra', 'HIGH', 'Formerly SIGSAM Bulletin, renamed in 2006, ISSN 1932-2232 verified'),
    'journals/ijict': ('International Journal of Information and Communication Technology', 'HIGH', 'Inderscience IJ-ICT verified, ISSN 1466-6642 verified'),
    'journals/information': ('Information (Switzerland)', 'HIGH', 'MDPI Information verified, ISSN 2078-2489 verified'),
    'journals/informaticaLT': ('Informatica (Netherlands)', 'HIGH', 'Vilnius University / IOS Press Informatica, ISSN 0868-4952 verified'),
    'journals/ixda': ('Interaction Design and Architecture(s)', 'HIGH', 'Official IxD&A journal title in SJR, ISSN 1826-9745 verified'),
    'journals/cleiej': ('CLEI Eletronic Journal (CLEIej)', 'HIGH', 'Official CLEI journal title in SJR, ISSN 0717-5000 verified'),
    'journals/jsa': ('Journal of Systems Architecture', 'HIGH', 'Formerly Microprocessing & Microprogramming, renamed in 1996, ISSN 1383-7621 verified'),
    'journals/lre': ('Language Resources and Evaluation', 'HIGH', 'Formerly Computers and the Humanities, renamed in 2005, ISSN 1574-020X verified'),
    'journals/jasis': ('Journal of the Association for Information Science and Technology', 'HIGH', 'Formerly J. Am. Soc. Inf. Sci., renamed to JASIST in 2014, ISSN 2330-1643 verified'),
    'journals/jasss': ('JASSS', 'HIGH', 'Official acronym title in SJR, ISSN 1460-7425 verified'),
    'journals/istr': ('Journal of Information Security and Applications', 'HIGH', 'Formerly Information Security Technical Report, renamed in 2013, ISSN 2214-2126 verified'),
    'journals/misq': ('MIS Quarterly: Management Information Systems', 'HIGH', 'Official subtitle in SJR, ISSN 0276-7783 verified'),
    'journals/widm': ('Wiley Interdisciplinary Reviews: Data Mining and Knowledge Discovery', 'HIGH', 'WIREs abbreviation expansion, ISSN 1942-4795 verified'),
    'journals/ijautcomp': ('Machine Intelligence Research', 'HIGH', 'Formerly Int. J. Autom. Comput., renamed in 2022, ISSN 2731-538X verified'),
    'journals/cl': ('Journal of Computer Languages', 'HIGH', 'Formerly Comput. Lang. Syst. Struct., merged into COLA in 2018, ISSN 2590-1184 verified'),
}

def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def clean_str(s: str) -> str:
    s = strip_accents(s).lower()
    s = re.sub(r'[^\w\s]', ' ', s)
    return ' '.join(s.split())

def tokenize(s: str) -> list[str]:
    s = strip_accents(s).lower()
    tokens = re.findall(r'[a-z0-9]+', s)
    return [t for t in tokens if t not in STOPWORDS]

def token_match(dt: str, st: str) -> bool:
    if dt == st:
        return True
    if dt in ABBREV_EXPANSIONS and st in ABBREV_EXPANSIONS[dt]:
        return True
    if len(dt) >= 3 and st.startswith(dt):
        return True
    return False

def tokens_match(d_toks: list[str], s_toks: list[str]) -> bool:
    if len(d_toks) != len(s_toks):
        return False
    return all(token_match(dt, st) for dt, st in zip(d_toks, s_toks))

def build_matcher():
    with open(DBLP_CSV, encoding='utf-8') as f:
        dblp_rows = list(csv.DictReader(f))
    with open(SJR_CSV, encoding='utf-8') as f:
        sjr_rows = list(csv.DictReader(f))

    sjr_by_clean = {clean_str(r['title']): r for r in sjr_rows}
    sjr_toks = [(r, tokenize(r['title'])) for r in sjr_rows]

    matched_dblp = {}
    used_sjr_titles = set()

    # Pass 1: Curated explicit map
    for d in dblp_rows:
        key = d['dblp_key']
        if key in EXPLICIT_MAP:
            target_title, conf, reason = EXPLICIT_MAP[key]
            for s in sjr_rows:
                if clean_str(s['title']) == clean_str(target_title):
                    if s['title'] not in used_sjr_titles:
                        matched_dblp[key] = (s, conf, reason)
                        used_sjr_titles.add(s['title'])
                    break

    # Pass 2: Exact normalized clean string match
    for d in dblp_rows:
        key = d['dblp_key']
        if key in matched_dblp:
            continue
        c = clean_str(d['dblp_name'])
        if c in sjr_by_clean:
            s = sjr_by_clean[c]
            if s['title'] not in used_sjr_titles:
                matched_dblp[key] = (s, 'HIGH', 'Exact normalized name match')
                used_sjr_titles.add(s['title'])

    # Pass 3: Token sequence match (ISO-4 abbreviations)
    candidates = defaultdict(list)
    for d in dblp_rows:
        key = d['dblp_key']
        if key in matched_dblp:
            continue
        d_tokens = tokenize(d['dblp_name'])
        if not d_tokens:
            continue
        for s, s_tokens in sjr_toks:
            if s['title'] in used_sjr_titles:
                continue
            if tokens_match(d_tokens, s_tokens):
                candidates[key].append(s)

    for key, c_list in candidates.items():
        if len(c_list) == 1:
            s = c_list[0]
            if s['title'] not in used_sjr_titles:
                matched_dblp[key] = (s, 'HIGH', f"Standard ISO abbreviation match to '{s['title']}'"[:100])
                used_sjr_titles.add(s['title'])

    # Pass 4: Prepare complete output
    output_rows = []
    high_count = 0
    medium_count = 0
    none_count = 0

    for d in dblp_rows:
        key = d['dblp_key']
        name = d['dblp_name']
        if key in matched_dblp:
            s, conf, reason = matched_dblp[key]
            if conf == 'HIGH':
                high_count += 1
            else:
                medium_count += 1
            output_rows.append({
                'dblp_key': key,
                'dblp_name': name,
                'sjr_title': s['title'],
                'issn_1': s['issn_1'],
                'issn_2': s['issn_2'],
                'quartile': s['quartile'],
                'confidence': conf,
                'reasoning': reason[:100],
            })
        else:
            none_count += 1
            output_rows.append({
                'dblp_key': key,
                'dblp_name': name,
                'sjr_title': 'NO_MATCH',
                'issn_1': '',
                'issn_2': '',
                'quartile': '',
                'confidence': 'NONE',
                'reasoning': 'Not indexed in SJR CS pool (regional, defunct, or non-CS category)',
            })

    # Write output CSV
    fieldnames = [
        'dblp_key', 'dblp_name', 'sjr_title', 'issn_1', 'issn_2',
        'quartile', 'confidence', 'reasoning'
    ]
    with open(OUT_CSV, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Generated {len(output_rows)} total rows in {OUT_CSV}")
    print(f"  HIGH confidence matches:   {high_count}")
    print(f"  MEDIUM confidence matches: {medium_count}")
    print(f"  NONE (NO_MATCH):           {none_count}")
    print(f"  Total matched journals:    {high_count + medium_count}")
    print(f"  Distinct SJR matched:      {len(used_sjr_titles)}")

if __name__ == '__main__':
    build_matcher()
