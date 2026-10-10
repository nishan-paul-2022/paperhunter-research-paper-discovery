"""
Configuration constants and venue mappings for dblp paper extraction.
"""
from pathlib import Path

BASE = "https://dblp.org/xml/"
DTD = "dblp.dtd"
XML_GZ = "dblp.xml.gz"
XML = "dblp.xml"
DB = "papers.db"
CSV = "papers.csv"

# dblp key prefix -> venue acronym (conference proceedings = <inproceedings>)
CONF = {
    "conf/sigcomm": "SIGCOMM", "conf/ccs": "CCS", "conf/sigecom": "EC",
    "conf/kdd": "KDD", "conf/mobicom": "MOBICOM", "conf/sigir": "SIGIR",
    "conf/sigsoft": "FSE", "conf/isca": "ISCA", "conf/mm": "ACMMM",
    "conf/siggraph": "SIGGRAPH", "conf/pods": "PODS", "conf/sosp": "SOSP",
    "conf/sigmod": "SIGMOD", "conf/podc": "PODC", "conf/stoc": "STOC",
    "conf/uist": "UIST", "conf/popl": "POPL", "conf/pldi": "PLDI",
    "conf/hri": "HRI", "conf/soda": "SODA", "conf/crypto": "CRYPTO",
    "conf/nips": "NeurIPS", "conf/asplos": "ASPLOS", "conf/acl": "ACL",
    "conf/kbse": "ASE", "conf/cav": "CAV", "conf/colt": "COLT",
    "conf/dac": "DAC", "conf/emnlp": "EMNLP", "conf/eccv": "ECCV",
    "conf/cvpr": "CVPR", "conf/vr": "VR", "conf/infocom": "INFOCOM",
    "conf/iccv": "ICCV", "conf/icdm": "ICDM", "conf/percom": "PERCOM",
    "conf/icra": "ICRA", "conf/focs": "FOCS", "conf/lics": "LICS",
    "conf/sp": "SP", "conf/ismar": "ISMAR", "conf/icalp": "ICALP",
    "conf/aips": "ICAPS", "conf/icaps": "ICAPS",  # ICAPS moved aips -> icaps
    "conf/icde": "ICDE", "conf/chi": "CHI", "conf/iclr": "ICLR",
    "conf/icml": "ICML", "conf/icse": "ICSE", "conf/kr": "KR",
    "conf/eurocrypt": "EuroCrypt", "conf/ijcai": "IJCAI",
    "conf/compgeom": "SoCG", "conf/hpca": "HPCA", "conf/micro": "MICRO",
    "conf/www": "WWW", "conf/sigmetrics": "SIGMETRICS", "conf/aaai": "AAAI",
    "conf/siggrapha": "SiggraphA", "conf/ndss": "NDSS", "conf/uss": "USENIX-Security",
    "conf/osdi": "OSDI",
}
