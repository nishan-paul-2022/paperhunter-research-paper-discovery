# Role rubrics: edge cases and worked decisions
Read once at start. Criteria are always judged on **evidence**, never on wording. Headline-contribution test decides roles.

## A. baseline vs everything else
| Situation | Decision |
|---|---|
| New multi-agent LLM framework that scans, exploits and escalates on VMs/CTF targets | core, `baseline` (if all four yes, published) |
| Agent paper + small supporting benchmark; agent is the headline | `baseline` (+ optionally `benchmark` second) |
| "X-Bench"/"X: a framework for evaluating LLMs on pentest", ships a baseline agent | core, `benchmark` (primary_contribution benchmark_dataset). NOT baseline |
| Study of how well GPT-x performs on CTFs / pentest, no new agent | core, `evaluation`. NOT baseline |
| LLM generates a single exploit/payload/PoC in one shot | related `exploit_generation_oneshot` (criteria 1/2 fail) |
| LLM agent that reproduces/exploits a CVE through an iterative tool loop in a container | core, `baseline` (exploit generation *within an agent loop*) |
| LLM only suggests next commands to a human (advisory copilot) | related (`offense_adjacent_other`), criterion 2 fails; if copilot executes tools with the human optional → agent, can pass |
| CTF-solving agent that executes actions in a loop | core, `baseline` (CTF exception) |
| CTF Q&A benchmark / static knowledge test | related `security_benchmark_static`, role benchmark only if tasks are offensive; else background |
| RL / MCTS pentest planner without LLM | related `rl_or_nonllm_pentest`, `background` |
| LLM + RL/MCTS/attack-graph planning for pentest | core baseline if LLM in the action loop |
| Fine-tuned/distilled pentest LLM used as agent | core baseline if used in an agent loop with execution |
| Smart-contract / binary / web / IoT / cloud exploitation agents | in scope; judge criteria 1-4 normally |
| Blue-team/defender agent only | related `defensive_agent` (`background`) or reject if unrelated to attack |
| Agent-vs-defender / adversary-emulation with LLM attacker | core, baseline (attacker is the headline) |
| Prompt injection / jailbreak of generic LLMs | reject; if it attacks *pentest agents* → related `attacks_on_agents` |
| Plain LLM vulnerability detection / code scanning | related `vuln_detection_llm` at most (often reject) |
| Intrusion-detection ML, malware classifiers, crypto hardware, unrelated domain | reject |
| Front matter, editorial, "Impressum", call for papers | reject + flag `frontmatter` |

## B. criteria 3 and 4 (soft): when `yes`, `partial`, `no`
- `black_box` **yes**: says black-box/zero-knowledge; or the agent gets only IP/URL/scope; or acts only through shell/tools; or gray-box. **no**: full source code/config or the exact exploit path is the main input. **partial**: setting mixed or only weakly implied. **unknown**: not inferable.
- `long_horizon` **yes**: any state/memory/context/hierarchical-plan/attack-graph/backtracking mechanism, or the agent iterates over several tool actions toward an attack goal (short chains count). **no**: one-shot action. **partial**: iteration suggested but unclear.
- Baseline needs both `yes`. Both `partial` or one `unknown/partial` with c1,c2 yes and agent headline → `baseline_candidate` (tier related, related_type `offense_adjacent_other`) with flags saying what is missing.

## C. survey / benchmark / evaluation specifics
- **survey core**: subject = LLM/AI agents for pentest/red team/offensive/CTF/exploit automation (any systematic review, SoK, taxonomy, roadmap, position paper on it). **related**: broad "LLMs for cybersecurity" surveys with a substantive offensive/agentic part (`llm_security_survey_broad`); "AI in pentesting" incl. non-LLM history; general agent surveys → `background` + `general_agent_framework`.
- **benchmark core**: environment/dataset where agents act (CTF suites run in sandboxes, vulnerable VM/AD ranges, CVE reproduction suites, pentest task sets). A benchmark of *knowledge questions* is related. A cyber range built for training humans is related `cyber_range_nonllm` unless evaluated with agents.
- **evaluation core**: measures existing LLMs/agents on offensive tasks: capability/risk/uplift studies, human-vs-AI, cost/failure analysis, replication. Qualitative opinion pieces are survey (position) rather than evaluation.
- For these three roles, criteria 1-2 are judged on the **systems under study**: a benchmark that only evaluates static Q&A fails c1/c2 → related, not core.

## D. publication status quick guide (DBLP-derived)
`journals/*` and `conf/*` entries are normally published. Check: real peer-reviewed venue, DOI/proceedings page, not front matter. Workshops: archival + co-located with top venue → `workshop_archival_toplevel`. CoRR/arXiv → `preprint_only` (and look for a published version).

## E. evidence ladder reminder
abstract → DOI landing → publisher page → Semantic Scholar/OpenAlex/Google Scholar/DBLP → arXiv → full text (intro, method, evaluation) → repo. Record the deepest level actually read in `evidence_level` and what you opened in `sources_checked`.

## F. common traps
- Title says "agent"/"LLM" but it's customer support, recommender, education → reject.
- Title says "attack"/"red team" but it's adversarial ML on images, or intrusion detection → reject (unless the abstract shows offensive autonomous operations by an LLM agent).
- "Penetration testing" tools without LLM (web scanners, Kali front-ends) → reject or related `rl_or_nonllm_pentest`; criterion 1 fails.
- Multi-agent RL with "red team" in a game/sim not about cyber → reject.
- A security-risk study of LLM-powered *apps* (not offensive agents) → reject or related `attacks_on_agents`.
