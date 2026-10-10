"""
Entry point when executing module as a script (python -m dblp).
"""
import os
import sys
from .cli import main

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main(sys.argv[1:])
