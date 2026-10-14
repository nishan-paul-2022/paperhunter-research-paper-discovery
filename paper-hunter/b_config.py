#!/usr/bin/env python3
"""
Configuration for the paper filtering pipeline
"""
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = REPO_ROOT / "paper-list"
PIPELINE_DIR = REPO_ROOT / "paper-hunter"
WORK_DIR = PIPELINE_DIR / "work"
OUTPUT_DIR = PIPELINE_DIR / "output"

# Ensure directories exist
WORK_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Research domain
RESEARCH_TOPIC = "LLM-Based Agentic Framework for Autonomous Black-Box Penetration Testing over Long-Horizon Attack Chains"

# Date range
MIN_YEAR = 2023
MAX_YEAR = 2026

# Fuzzy matching keywords (stage 1)
# These are organized by category for better matching
KEYWORDS = {
    # AI/LLM terms
    "ai_agent": [
        "llm", "large language model", "gpt", "language model",
        "ai agent", "autonomous agent", "agentic", "multi-agent",
        "tool-augmented", "react", "chain-of-thought", "reasoning",
        "copilot", "assistant", "chatbot"
    ],
    
    # Offensive security terms
    "offensive_security": [
        "penetration test", "pentesting", "pentest", "red team", "red-team",
        "adversary emulation", "ethical hacking", "cyber attack", "intrusion",
        "exploitation", "exploit", "vulnerability assessment", "vapt",
        "attack automation", "offensive security", "security testing",
        "cyber operations", "attack chain"
    ],
    
    # CTF and challenges
    "ctf": [
        "ctf", "capture the flag", "cyber challenge", "cyber competition",
        "hack challenge", "pwn", "reverse engineering", "binary exploitation"
    ],
    
    # Black-box / testing approaches
    "testing_approach": [
        "black-box", "black box", "gray-box", "grey-box", "zero-knowledge",
        "blind testing", "reconnaissance", "recon", "scanning", "enumeration"
    ],
    
    # Long-horizon / planning
    "planning": [
        "multi-step", "multi-stage", "long-horizon", "attack chain",
        "attack graph", "attack path", "planning", "hierarchical",
        "task decomposition", "goal-oriented", "sequential attack"
    ],
    
    # System types
    "system_type": [
        "framework", "system", "tool", "platform", "architecture",
        "benchmark", "dataset", "testbed", "evaluation"
    ]
}

# Required combinations for fuzzy pass
# At least one from AI + one from Security
REQUIRED_CATEGORIES = {
    "must_have_ai": ["ai_agent"],
    "must_have_security": ["offensive_security", "ctf"]
}

# Exclusion keywords (immediate reject)
EXCLUSION_KEYWORDS = [
    "intrusion detection", "intrusion prevention", "ids", "ips",
    "malware detection", "anomaly detection", "defensive",
    "firewall", "antivirus", "spam detection", "phishing detection",
    "fraud detection", "network defense", "blue team", "blue-team"
]

# Fields expected in input CSV
INPUT_CSV_FIELDS = [
    "dblp_key",
    "title", 
    "authors",
    "year",
    "venue",
    "ee",  # DOI URL
    "url"  # DBLP URL
]

# Fields in output decision files
DECISION_FIELDS = [
    "id",
    "dblp_key",
    "title",
    "authors",
    "year",
    "venue",
    "doi",
    "url",
    "tier",  # core | related | reject | needs_manual
    "roles",  # list: baseline, survey, benchmark, evaluation, background
    "primary_contribution",  # agent_system | benchmark_dataset | evaluation_study | survey | other
    "publication_status",  # published | workshop_archival_toplevel | workshop_other | preprint_only | thesis_or_report | unknown
    "criteria",  # dict with llm_agent, offensive_security, black_box, long_horizon
    "evidence",  # verbatim quote <= 25 words
    "reason",  # explanation <= 30 words
    "abstract_source",  # provided | web | none
    "doi_verified",  # true | false | null
    "confidence",  # 0.0 - 1.0
    "stage"  # fuzzy | screen1 | screen2 | verify
]

# Batch size for AI processing
BATCH_SIZE = 10

# AI agent command (for stage 2)
# This will be passed the prompt file and batch of papers
AI_AGENT_COMMAND = "your-ai-agent-command-here"  # Update this
