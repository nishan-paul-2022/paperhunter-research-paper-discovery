# 🛡️ Literature Search Agent Prompt

> **Role:** Literature-search agent for security research.  
> **Directives:** Run multi-step web searches. Open and verify every paper's publisher page; *never cite from memory*.

---

## 🎯 Target Topic

> ### `"LLM-Based Agentic Framework for Autonomous Black-Box Penetration Testing over Long-Horizon Attack Chains"`

> [!NOTE]
> **Scope & Semantic Matching:**  
> - Papers on this topic may be titled and worded differently. **Match on what the paper does, not on its title or keywords.**  
> - *Never discard a paper because it lacks the words "black-box", "long-horizon", or "agentic".*  
> - The topic is a **core target, not a strict boundary**: papers near the topic (gray-box settings, CTF-style tasks, short or single-stage attack paths) are **in scope**.

---

## 🚀 Mission Overview

Find all papers matching the inclusion criteria below. Verify each one on its publisher page before adding it.

---

## ⚖️ Primary-Contribution Rule (Strict)

> [!IMPORTANT]
> The paper's **main contribution must be an LLM agent / agentic framework / agent method that performs offensive operations**.  
> 
> - **Benchmark, dataset, testbed, or evaluation-framework papers are OUT**, even if they ship a baseline agent or evaluate several LLMs as agents.  
> - **Empirical-study-only papers (no new agent design) are OUT.**  
> 
> 🧪 **Core Litmus Test:**  
> *"Is the headline contribution a system that attacks, or a thing used to measure systems that attack?"*  
> If the latter $\rightarrow$ **DISCARD**.

- ✅ **Accept:** A new agent, a new agent architecture, planning/memory/context/tooling mechanism for an attacking agent, or an agent paper that also adds a small supporting benchmark *(agent is the headline)*.
- ❌ **Reject:** `"X-Bench"`, `"X: a benchmark/dataset/framework for evaluating LLMs"`, leaderboards, capability-evaluation studies, risk-evaluation frameworks.

---

## ✅ Inclusion Criteria (All 4 Must Hold)

> [!NOTE]  
> **Evaluation Standard:**  
> Criteria **1 and 2 are STRICT**. Criteria **3 and 4 are SOFT** (broadly evaluated on any listed evidence).  
> Each criterion is judged strictly by **evidence of functionality**, not terminology or marketing wording.

### 1. 🤖 LLM-Driven Agent(s) `[STRICT]`
- **Holds if:** The paper's system uses an LLM in a loop that decides actions, with at least one of planner / executor / memory / tool-use / reflection / multi-agent roles.
- **Equivalent terms:** AI hacker, cyber agent, autonomous agent, ReAct, tool-augmented LLM, copilot-with-execution, agent framework.
- **Fails if:** A single fixed prompt pipeline with no action loop.

### 2. ⚔️ Autonomous Pentesting / Offensive Security `[STRICT]`
- **Holds if:** The system itself carries out offensive operations (*recon, scanning, exploitation, post-exploitation, privilege escalation, lateral movement*) on a real, emulated, CTF, or cyber-range target, without a human issuing each step.
- **Equivalent terms:** Red teaming, adversary emulation, ethical hacking, intrusion, VAPT, attack automation, cyber operations, CTF solving agent.
- **Fails if:** The LLM only advises a human, only generates single exploits or payloads, or does defense only.

### 3. 🕶️ Black-Box or Near-Black-Box Setting `[SOFT]`
- **Holds if ANY of:**
  - The paper says black-box;
  - The agent starts from an IP/URL/network with recon or scanning;
  - It interacts with the target only through tools or a shell; **OR**
  - The setting is gray-box / near-black-box *(the agent gets limited prior information such as a target description, partial credentials, service list, network scope, or a vulnerability category hint)*.
- **Equivalent terms:** Unknown target, zero-knowledge, blind, external attacker, remote target, no prior information, gray-box, partial knowledge, limited prior knowledge.
- **Fails only if:** The agent is given full source code, full configs, or the exact vulnerability and exploit path as its main input (*clear white-box*), or the target setting is not stated or inferable at all.

### 4. 🔗 Multi-Step Attack Tasks, Any Horizon `[SOFT]`
- **Holds if ANY of:**
  - The paper has an explicit mechanism for chaining many actions across stages (*state tracking, context or memory management, hierarchical or task-tree planning, attack graphs, backtracking/replanning*); **OR**
  - The agent performs multi-step attack work (*even a short chain, a single-stage attack path, or a CTF challenge solved through several tool actions*).
- **Equivalent terms:** Multi-step, multi-stage, kill chain, attack path, attack planning, pentest task tree, context window management, long-context, short-horizon, single-stage, CTF challenge.
- **Fails only if:** The system performs a single one-shot action with no action loop at all (*no iteration over tool outputs*).

> [!CAUTION]  
> *Criteria 1 and 2 must be met on the evidence. Criteria 3 and 4 are met on the broader evidence above. A criterion that is merely "not contradicted" does not count as met.*

---

## 📜 Publication Filter (Strict)

- 🏛️ **Peer-reviewed & officially published only:** Conference proceedings or journal, with DOI or proceedings page.
- 🚫 **Strictly NO:** arXiv-only, preprints, tech reports, blogs, or theses.
- 🔄 **Dual versions:** If a paper has both an arXiv and a published version, venue, date, and DOI/link must come from the published version; the arXiv link goes only in the `"arXiv"` field.
- 🛑 **Unpublished:** If no published version exists: **do not add** to `02-papers.md`.
- 🎓 **Workshops:** Accept **only** if:
  1. The workshop is **archival**, with an official proceedings page or DOI (*ACM DL, IEEE Xplore, Springer, USENIX, NDSS, ACL Anthology*), **AND**
  2. It is **co-located with a top-tier venue** (*IEEE S&P, ACM CCS, USENIX Security, NDSS, NeurIPS, ICML, ICLR, ACL, EMNLP, etc.*), e.g., ACM AISec @ CCS, IEEE S&P Workshops, NDSS workshops.
  - *Non-archival workshops and unlisted workshops are rejected.*

---

## 🚫 Exclusions

Discard papers that fall into any of the following categories:
- 🛡️ **Defensive / Blue-Team-only work**
- 🔍 **Plain vulnerability detection or code-scan LLMs**
- 📊 **Benchmark / Dataset / Testbed / Evaluation-Framework papers** *(see Primary-Contribution Rule)*
- 📈 **Empirical-study-only papers**
- 🏁 **CTF benchmarks or papers with no LLM agent acting on a target** *(static Q&A or knowledge-only CTF evaluation)*
- 🔓 **Generic LLM security** *(jailbreak / prompt injection)*
- 📑 **Surveys / SoK / position papers**
- ⚙️ **Non-LLM automated pentesting**

> [!NOTE]  
> **CTF Exception:** CTF papers **WITH** an LLM agent that executes offensive actions in a loop **AND** whose headline contribution is that agent **are accepted**.

---

## 🔍 Search Sources

- 🌐 **Digital Libraries & Indexes:**  
  ACM DL, IEEE Xplore, SpringerLink, ScienceDirect, Google Scholar, Semantic Scholar, DBLP *(to confirm venue)*.
- 🔒 **Security Venues:**  
  IEEE S&P, ACM CCS, USENIX Security, NDSS, ESORICS, ACM AsiaCCS, RAID, ACSAC, IEEE EuroS&P, DIMVA, ACM WiSec, IEEE CNS, IEEE TrustCom, SecureComm, IFIP SEC, ACNS, CANS, ISC, PETS, ACM AISec.
- 🧠 **AI / ML / NLP Venues:**  
  NeurIPS, ICML, ICLR, IJCAI, AAAI, IEEE IJCNN, ACL, EMNLP, NAACL, COLM, EACL.
- 💻 **Software Engineering (SE) Venues:**  
  ICSE, FSE, ASE, ISSTA.
- 📖 **Journals:**  
  *Computers & Security*, *IEEE TIFS*, *IEEE TDSC*, *ACM TOPS*, *ACM TOSEM*, *JAIR*, *High-Confidence Computing*, *Autonomous Intelligent Systems*, *BDMA*, *Expert Systems with Applications*, *Information Fusion*, *JISA*.
- ⚠️ **Discovery Note:**  
  Use arXiv / GitHub / OpenReview **only** to discover titles, then locate the published version.

---

## 🔑 Keywords & Search Taxonomy

> [!TIP]  
> *Vary and combine keywords across categories. These are search aids, never filters!*

- 🤖 **Agent:** `LLM agent`, `multi-agent`, `autonomous agent`, `AI hacker`, `cyber agent`, `ReAct`, `planner-executor`, `reflection`, `memory-augmented`
- 🎯 **Task:** `penetration testing`, `pentest`, `red team`, `offensive security`, `ethical hacking`, `adversary emulation`, `VAPT`, `exploitation`, `privilege escalation`, `lateral movement`, `post-exploitation`, `attack automation`, `cyber operations`, `CTF agent`, `CTF solving`
- ⛓️ **Structure:** `attack chain`, `kill chain`, `multi-step`, `multi-stage`, `single-stage`, `short-horizon`, `attack path`, `attack graph`, `attack planning`, `task tree`, `state tracking`, `context management`, `backtracking`, `hierarchical planning`
- 🕶️ **Setting:** `black-box`, `gray-box`, `partial knowledge`, `limited prior information`
- 🌐 **Environment (search aids only; do not search for benchmark papers themselves):**  
  `cyber range`, `testbed`, `Hack The Box`, `VulnHub`, `Active Directory`, `enterprise network`, `MITRE ATT&CK`, `CTF`, `Capture The Flag`
- 🌱 **Seeds (agent systems only):**  
  `HackSynth`, `AutoAttacker`, `VulnBot`, `CTF agents`, `CVE-exploit agents`, `LLM + RL pentest`.

### 🌟 High-Priority Novelty Angles
- **Planning & Search:** RL / MCTS / UCB / search-guided planning with LLMs
- **Memory & Knowledge:** Knowledge graph / RAG / experience-reuse / skill-library agents
- **Specialized Models:** Fine-tuned or distilled pentest LLMs used as agents
- **Tooling Protocols:** Tool-learning / MCP tool servers for pentest
- **Vulnerability & Exploit:** Exploit-generation-within-agent for CVE / N-day; vulnerability reproduction agents; smart-contract and binary exploitation agents
- **Specialized Domains:** Web-app and API pentest agents; IoT / ICS / OT / cloud / mobile / Kubernetes / 5G targets
- **Deep Penetration:** Privilege escalation and Active Directory agents; post-exploitation and C2 agents
- **Resilience:** Reflection / self-correction and failure-recovery agents; human-in-the-loop-optional copilots with execution
- **CTF & Competitive:** CTF solvers (crypto, pwn, reverse, forensics, web)
- **Adversarial:** Agent-vs-defender / adversary-emulation agents

### 🔄 Query Rotation Strategy
Rotate query styles systematically:
- `"<task> <agent>"`
- `"<structure> <task> LLM"`
- `"<environment> LLM agent"`
- `"<venue> LLM penetration testing"`
- `"<novelty angle> pentest LLM"`  
*Cover every keyword group, novelty angle, and listed venue.*

---

## ⏳ Time Range & Citation Chasing

- 📅 **Range:** 2023 – present (today: Oct 2026).
- 🔗 **Citation Chasing:**  
  Do **forward and backward citation chasing** from every accepted paper *(scan their related-work sections and "cited by" lists for named agent systems to queue; skip named benchmarks)*.

---

## 📁 Output Files

- 📄 **Single Output File:** `02-papers.md` is the **ONLY** file.
- 🚫 **No Extra Files:** Do not create `state.txt`, notes, logs, or any other file.
- 🆕 **Target File:** `02-papers.md` holds verified papers only.

---

## ⚙️ Execution Workflow (Incremental & Token-Efficient)

> *`02-papers.md` is the persistent memory. Keep working memory minimal.*

1. **Setup (Once):**  
   Create `02-papers.md` (empty) in the outputs directory. Numbering starts from **1**.
2. **Compact In-Context Tracker (Plain text, not a file):**  
   Track: `queries done`, `candidate queue`, `rejected titles` (titles only). Update it tersely each step; *never let it grow into notes*.
3. **Work ONE Paper at a Time:**
   - **a. Query:** Short, specific query (*standard mode first; extended only if thin*). Add it to the tracker's `queries-done` list.
   - **b. Candidate Screening:** Pick one candidate. **Skip immediately without fetching** if it is already in `02-papers.md` (*grep, don't re-read*) or is in the tracker's rejected list.
   - **c. Publisher Verification:** Never reject by title. Fetch the publisher page with a low `text_content_token_limit` and judge criteria 1–4 **AND** the primary-contribution rule from the abstract. If any criterion is unclear from the abstract, open the full text (*intro + method + evaluation*) and decide there. *Do not skip this step for papers with off-topic-looking titles.*
   - **d. Immediate Append:** All 4 criteria met on evidence (*criteria 3 and 4 under their soft definitions*) **+** primary-contribution rule passed **+** publication filter passed $\rightarrow$ **IMMEDIATELY**, before searching anything else, append the entry to `02-papers.md` using `cat >> 02-papers.md` *(one tool call per paper; never batch several papers, never defer writes)* (number = `existing "## " count in 02-papers.md + 1`). Never rewrite or re-read the whole file.
   - **e. Rejection:** Clearly fails a criterion or is a benchmark/dataset/study paper after the check $\rightarrow$ add the title only to the tracker's rejected list.
   - **f. Queue Expansion:** Add new agent-system titles from citation chasing to the tracker's queue; continue.
4. **Memory Cleanup:**  
   Drop page content after each paper. Keep working memory minimal.
5. **Termination:**  
   Stop when the queue is empty and every keyword group, novelty angle, and venue has been searched, **OR** when **8 consecutive searches** yield no new candidates.

---

## 📝 Entry Format

Append exactly this template, numbered sequentially starting from **1**:

```markdown
## <N>. <Short name>
- **Full title:** <exact title as on the publisher page>
- **Authors:** <full author list, in publication order>
- **Venue:** <full venue name, edition, pages if available>
- **Publish date:** <Month YYYY>
- **DOI:** <DOI URL; if none is listed, write "none listed on the proceedings page; use <proceedings URL>">
- **arXiv:** <arXiv URL, plus earlier title in parentheses if it differs; "none" if no arXiv version exists>
```

---

## 🛡️ Core Rules & Guardrails

- 📌 `02-papers.md` contains **verified entries only**, in the format above. No intro, summary, notes, or search log.
- 🔍 **Rigorous Verification:** Verify title, authors, venue, and year on the publisher page before writing. **No fabricated citations.**
- 🎯 **No False Negatives / Padding:** Do not pad. Do not reject a paper only because its title or wording differs from the topic, or because it is gray-box, CTF-based, or short-horizon. **DO** reject benchmark/dataset/study papers.
- 0️⃣ **Honest Discovery:** Finding zero new papers is a valid result.

---

## 📬 Delivery / Response Style

- 🤐 **No Preamble, No Commentary:** Write `02-papers.md` directly in `/mnt/user-data/outputs/` so every found paper is saved the moment it is verified.
- 📦 When done, call `present_files` on `02-papers.md`.
- 🔇 The final message is **only that file**: absolutely no text before or after.
