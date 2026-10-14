#!/usr/bin/env python3
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = REPO_ROOT / "paper-list"
PIPELINE_DIR = REPO_ROOT / "paper-hunter"
WORK_DIR = PIPELINE_DIR / "work"
OUTPUT_DIR = PIPELINE_DIR / "output"
WORK_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RESEARCH_TOPIC = "LLM-Based Agentic Framework for Autonomous Black-Box Penetration Testing over Long-Horizon Attack Chains"
MIN_YEAR = 2023
MAX_YEAR = 2026
KEYWORDS = {
    "ai_agent": [
        "llm", "large language model", "gpt", "language model",
        "ai agent", "autonomous agent", "agentic", "multi-agent",
        "tool-augmented", "react", "chain-of-thought", "reasoning",
        "copilot", "assistant", "chatbot"
    ],
    "offensive_security": [
        "penetration test", "pentesting", "pentest", "red team", "red-team",
        "adversary emulation", "ethical hacking", "cyber attack", "intrusion",
        "exploitation", "exploit", "vulnerability assessment", "vapt",
        "attack automation", "offensive security", "security testing",
        "cyber operations", "attack chain"
    ],
    "ctf": [
        "ctf", "capture the flag", "cyber challenge", "cyber competition",
        "hack challenge", "pwn", "reverse engineering", "binary exploitation"
    ],
    "testing_approach": [
        "black-box", "black box", "gray-box", "grey-box", "zero-knowledge",
        "blind testing", "reconnaissance", "recon", "scanning", "enumeration"
    ],
    "planning": [
        "multi-step", "multi-stage", "long-horizon", "attack chain",
        "attack graph", "attack path", "planning", "hierarchical",
        "task decomposition", "goal-oriented", "sequential attack"
    ],
    "system_type": [
        "framework", "system", "tool", "platform", "architecture",
        "benchmark", "dataset", "testbed", "evaluation"
    ]
}
REQUIRED_CATEGORIES = {
    "must_have_ai": ["ai_agent"],
    "must_have_security": ["offensive_security", "ctf"]
}
EXCLUSION_KEYWORDS = [
    "intrusion detection", "intrusion prevention", "ids", "ips",
    "malware detection", "anomaly detection", "defensive",
    "firewall", "antivirus", "spam detection", "phishing detection",
    "fraud detection", "network defense", "blue team", "blue-team"
]
INPUT_CSV_FIELDS = [
    "dblp_key",
    "title", 
    "authors",
    "year",
    "venue",
    "ee",
    "url"
]
DECISION_FIELDS = [
    "id",
    "dblp_key",
    "title",
    "authors",
    "year",
    "venue",
    "doi",
    "url",
    "tier",
    "roles",
    "primary_contribution",
    "publication_status",
    "criteria",
    "evidence",
    "reason",
    "abstract_source",
    "doi_verified",
    "confidence",
    "stage"
]
BATCH_SIZE = 10
AI_AGENT_COMMAND = "your-ai-agent-command-here"
