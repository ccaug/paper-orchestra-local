#!/usr/bin/env python
"""Fake OpenAI-compatible server to DRY-RUN the whole writing pipeline without a real model.
It returns canned (but structurally valid) answers for the outline, literature synthesis and section
writing stages. Use it only to test the plumbing. Usage: python tools/fake_local_llm.py [port]"""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUTLINE = {
 "plotting_plan": [{"figure_id": "fig_convergence", "title": "Convergence", "plot_type": "plot", "data_source": "experimental_log.md",
                    "objective": "Show faster convergence of TRACE", "aspect_ratio": "4:3"}],
 "intro_related_work_plan": {
  "introduction_strategy": {"hook_hypothesis": "Curriculum learning orders training examples from easy to hard.",
                            "problem_gap_hypothesis": "Difficulty scores from a single snapshot of the model are noisy early in training.",
                            "search_directions": ["foundational curriculum learning papers", "difficulty scores for training examples"]},
  "related_work_strategy": {"overview": "Curriculum learning and example difficulty.",
   "subsections": [
    {"subsection_title": "2.1 Curriculum and self-paced learning", "methodology_cluster": "curriculum, self-paced learning, pacing functions",
     "sota_investigation_mission": "Find the main curriculum learning methods", "limitation_hypothesis": "They rely on a single loss snapshot.",
     "limitation_search_queries": ["curriculum learning pacing function deep networks", "self-paced learning loss threshold"],
     "bridge_to_our_method": "TRACE uses the loss trajectory instead of one snapshot."},
    {"subsection_title": "2.2 Example difficulty and data pruning", "methodology_cluster": "forgetting events, EL2N, data pruning",
     "sota_investigation_mission": "Find difficulty scores computed during training", "limitation_hypothesis": "Scores ignore the temporal trend.",
     "limitation_search_queries": ["example forgetting events difficulty", "EL2N data pruning early training"],
     "bridge_to_our_method": "TRACE adds the slope of the loss trajectory."}]}},
 "section_plan": [
  {"section_title": "Abstract", "subsections": [{"subsection_title": "Abstract Content", "content_bullets": ["problem", "method", "results"], "citation_hints": []}]},
  {"section_title": "3. Method", "subsections": [{"subsection_title": "3.1 TRACE score", "content_bullets": ["window mean", "loss slope"], "citation_hints": ["Kingma and Ba (Adam)"]},
                                              {"subsection_title": "3.2 Pacing", "content_bullets": ["linear pacing"], "citation_hints": ["Hacohen and Weinshall"]}]},
  {"section_title": "4. Experiments", "subsections": [{"subsection_title": "4.1 Setup", "content_bullets": ["CIFAR-10", "ResNet-18"], "citation_hints": ["CIFAR-10 dataset", "ResNet"]},
                                                    {"subsection_title": "4.2 Results", "content_bullets": ["main table", "ablation"], "citation_hints": []}]},
  {"section_title": "5. Conclusion", "subsections": [{"subsection_title": "5.1 Conclusion", "content_bullets": ["summary", "limitations"], "citation_hints": []}]}]}


def lit_synth(user):
    payload, _ = json.JSONDecoder().raw_decode(user.split("\n\n", 1)[1])
    tpl, keys = payload["template.tex"], payload["citation_checklist"]
    k = lambda a, b: ",".join(keys[a:b]) or ""
    intro = ("Training a network on examples ordered from easy to hard can speed up learning \\citep{%s}. Yet most difficulty scores come "
             "from a single snapshot of the model, which is noisy early in training \\citep{%s}. We propose TRACE, which scores each example "
             "by the evolution of its loss.\n\n\\textbf{Contributions.} (1) a trajectory-based difficulty score; (2) a simple pacing schedule; "
             "(3) an empirical study on CIFAR-10 \\citep{%s}." % (k(0, 2), k(2, 5), k(5, 7)))
    rw = ("\\subsection{Curriculum and self-paced learning}\nCurriculum learning \\citep{%s} and self-paced learning \\citep{%s} order or select "
          "examples by difficulty.\n\n\\subsection{Example difficulty and data pruning}\nForgetting events and early error norms act as difficulty "
          "signals \\citep{%s}; an optimizer such as Adam \\citep{%s} is commonly used." % (k(0, 1), k(1, 2), k(3, 5), k(7, 8)))
    tpl = tpl.replace("Introduction here.", intro).replace("Related work here.", rw)
    return "```latex\n" + tpl + "\n```"


def writer(user):
    m = re.search(r"--- INPUT: template\.tex \(DO NOT CHANGE EXISTING TEXT\. FILL MISSING SECTIONS\.\) ---\n(.*?)\n\n\*\*INSTRUCTION\*\*", user, re.S)
    tpl = m.group(1)
    keys = re.findall(r"--- Key: (\S+) ---", user)
    c = lambda i: keys[i] if i < len(keys) else keys[0]
    abstract = ("We introduce TRACE, a curriculum method that scores training examples by the evolution of their loss over a sliding window. "
                "On CIFAR-10 with ResNet-18, TRACE reaches 93.5\\% test accuracy versus 92.4\\% for random order and clearly demonstrates faster "
                "convergence, reaching 90\\% accuracy 9 epochs earlier.")
    method = ("\\subsection{TRACE score}\nFor each example we keep its loss over the last $w$ epochs and combine the window mean with the "
              "negative slope of a linear fit. \\subsection{Pacing}\nThe eligible fraction of the data grows linearly from 40\\%% to 100\\%% "
              "over the first 60 epochs \\citep{%s}." % c(2))
    exp = ("\\subsection{Setup}\nWe train ResNet-18 \\citep{%s} on CIFAR-10 \\citep{%s} for 100 epochs with 5 seeds.\n\n"
           "\\subsection{Results}\n\\begin{table}[t]\n\\centering\n\\caption{Main results on CIFAR-10 (test accuracy \\%%, 5 seeds).}\n\\label{tab:main}\n"
           "\\begin{tabular}{lcc}\n\\toprule\nMethod & Accuracy & Epochs to 90\\%% \\\\\n\\midrule\nRandom order & 92.4 $\\pm$ 0.2 & 48 \\\\\n"
           "Self-paced & 92.6 $\\pm$ 0.3 & 46 \\\\\nForgetting & 92.9 $\\pm$ 0.2 & 44 \\\\\nEL2N & 92.8 $\\pm$ 0.3 & 45 \\\\\n"
           "TRACE (ours) & 93.5 $\\pm$ 0.2 & 39 \\\\\n\\bottomrule\n\\end{tabular}\n\\end{table}\n\n"
           "TRACE significantly outperforms every baseline in all settings (Table~\\ref{tab:main}). Figure~\\ref{fig:conv} shows the convergence.\n\n"
           "\\begin{figure}[t]\n\\centering\n\\includegraphics[width=0.8\\linewidth]{figures/fig_convergence.png}\n"
           "\\caption{Test accuracy versus epoch.}\n\\label{fig:conv}\n\\end{figure}" % (c(5), c(6))).replace("%%", "%")
    concl = ("TRACE always improves training and proves that loss trajectories are the right difficulty signal. A limitation is that we only "
             "tested CIFAR-10.")
    tpl = (tpl.replace("Abstract here.", abstract).replace("Methodology here.", method)
              .replace("Experimental setup, results, and analysis here.", exp).replace("Conclusion here.", concl))
    return "```latex\n" + tpl + "\n```"


def answer(msgs):
    text = "\n\n".join(m["content"] if isinstance(m["content"], str) else " ".join(p.get("text", "") for p in m["content"]) for m in msgs)
    time.sleep(0.8)
    if "Return the **full** compilable LaTeX file" in text:
        return writer(text)
    if "Generate the LaTeX for Introduction and Related Work" in text:
        return lit_synth(text[text.index("Generate the LaTeX for Introduction and Related Work"):])
    if "plotting_plan" in text:
        return "```json\n" + json.dumps(OUTLINE, indent=2) + "\n```"
    return "OK"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "fake-local"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = answer(req["messages"])
        if req.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for i in range(0, len(out), 60):
                ch = {"choices": [{"index": 0, "delta": {"content": out[i:i + 60]}}]}
                self.wfile.write(f"data: {json.dumps(ch)}\n\n".encode()); self.wfile.flush(); time.sleep(0.02)
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            body = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": out}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11500
    print(f"fake local LLM on http://127.0.0.1:{port}/v1  (dry run only)")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
