"""
Phase 7 Verification Suite:
Coding Practice Mode (q_type: "code"), Zero-DOM Anti-Detection for learning.ccbp.in/question/*,
Monaco/Ace MAIN-World Editor Bridge, and Python/C++/Java AST & Signature Verification.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Ensure workspace root is in sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

os.environ["QUIZZY_OFFLINE_TEST_MODE"] = "1"

import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.core.normalizer import (
    NormalizationError,
    check_balanced_brackets,
    extract_starter_signatures,
    normalize_answer,
    normalize_code_answer,
)
from backend.cache import compute_cache_key, get_cached_solution, cache_solution
from backend.schemas import SolveRequest


def test_1_zero_dom_stealth_on_coding_pages():
    """
    Test 1: Zero-DOM Stealth on Coding Pages.
    Verify in Node.js VM that loading floating_hud.js and extractor.js on a simulated
    learning.ccbp.in/question/123 DOM with a MutationObserver on document.documentElement
    records exactly 0 DOM mutations.
    """
    print("\n[Phase 7 - Test 1] Verifying Zero-DOM Stealth on simulated coding pages...")

    floating_hud_path = (WORKSPACE_ROOT / "extension" / "floating_hud.js").as_posix()
    extractor_path = (WORKSPACE_ROOT / "extension" / "extractor.js").as_posix()

    node_script = f"""
const fs = require('fs');

// 1. Setup Mock DOM with strict MutationObserver tracking
const mutations = [];

function matchSingle(node, s) {{
  if (!s || !node || node.nodeType !== 1) return false;
  if (s.startsWith(".")) return (node.className || "").split(/\\s+/).includes(s.slice(1));
  if (s.startsWith("#")) return (node.getAttribute("id") || "") === s.slice(1);
  if (s.startsWith("[")) {{
    const clean = s.replace(/[\\[\\]'"]/g, "");
    if (clean.includes("*=")) {{
      const parts = clean.split("*=");
      return (node.getAttribute(parts[0]) || "").includes(parts[1]);
    }}
    if (clean.includes("=")) {{
      const parts = clean.split("=");
      return node.getAttribute(parts[0]) === parts[1];
    }}
    return node.getAttribute(clean) !== null;
  }}
  return node.tagName.toLowerCase() === s.toLowerCase();
}}

function matchesSelector(node, sList) {{
  if (!sList) return false;
  const parts = sList.split(",").map(p => p.trim());
  return parts.some(p => matchSingle(node, p));
}}

class MockNode {{
  constructor(tagName, isText = false) {{
    this.isText = isText;
    if (isText) {{
      this.nodeType = 3;
      this.nodeValue = String(tagName);
      this.childNodes = [];
    }} else {{
      this.nodeType = 1;
      this.tagName = String(tagName).toUpperCase();
      this.attributes = {{}};
      this.className = "";
      this.childNodes = [];
      this.parentElement = null;
      this.parentNode = null;
      this.style = {{}};
    }}
  }}

  get id() {{
    return this.getAttribute("id") || "";
  }}

  set id(v) {{
    this.setAttribute("id", v);
  }}

  get children() {{
    return this.childNodes.filter(c => c.nodeType === 1);
  }}

  appendChild(child) {{
    mutations.push({{ type: "appendChild", target: this.tagName, child: child.tagName || child.nodeValue }});
    this.childNodes.push(child);
    child.parentElement = this;
    child.parentNode = this;
    return child;
  }}

  insertBefore(newChild, refChild) {{
    mutations.push({{ type: "insertBefore", target: this.tagName, child: newChild.tagName }});
    const idx = this.childNodes.indexOf(refChild);
    if (idx !== -1) {{
      this.childNodes.splice(idx, 0, newChild);
    }} else {{
      this.childNodes.push(newChild);
    }}
    newChild.parentElement = this;
    return newChild;
  }}

  removeChild(child) {{
    mutations.push({{ type: "removeChild", target: this.tagName, child: child.tagName }});
    const idx = this.childNodes.indexOf(child);
    if (idx !== -1) this.childNodes.splice(idx, 1);
    child.parentElement = null;
    return child;
  }}

  setAttribute(k, v) {{
    mutations.push({{ type: "setAttribute", target: this.tagName, key: k, val: v }});
    this.attributes[k] = String(v);
    if (k === "class") this.className = String(v);
  }}

  getAttribute(k) {{
    if (k === "class") return this.className;
    return this.attributes[k] || null;
  }}

  querySelector(sel) {{
    return this.querySelectorAll(sel)[0] || null;
  }}

  querySelectorAll(sel) {{
    const res = [];
    function walk(node) {{
      if (!node || node.nodeType !== 1) return;
      if (matchesSelector(node, sel)) res.push(node);
      for (const c of node.childNodes) walk(c);
    }}
    for (const c of this.childNodes) walk(c);
    return res;
  }}

  closest(sel) {{
    let cur = this;
    while (cur) {{
      if (cur.nodeType === 1 && matchesSelector(cur, sel)) return cur;
      cur = cur.parentElement;
    }}
    return null;
  }}

  contains(other) {{
    let cur = other;
    while (cur) {{
      if (cur === this) return true;
      cur = cur.parentElement;
    }}
    return false;
  }}

  attachShadow() {{
    mutations.push({{ type: "attachShadow", target: this.tagName }});
    const s = new MockNode("shadow-root");
    this.shadowRoot = s;
    return s;
  }}

  addEventListener(ev, fn) {{
    this._listeners = this._listeners || {{}};
    this._listeners[ev] = this._listeners[ev] || [];
    this._listeners[ev].push(fn);
  }}

  removeEventListener() {{}}
}}

// 2. Build mock page representing learning.ccbp.in/question/42
const docElement = new MockNode("html");
const head = new MockNode("head");
const body = new MockNode("body");
docElement.appendChild(head);
docElement.appendChild(body);

// Coding description pane
const desc = new MockNode("div");
desc.className = "question-description";
const h1 = new MockNode("h1");
h1.appendChild(new MockNode("Add Two Numbers", true));
const p = new MockNode("p");
p.appendChild(new MockNode("Given two numbers A and B, print their sum.", true));
desc.appendChild(h1);
desc.appendChild(p);
body.appendChild(desc);

// Language dropdown
const selLang = new MockNode("select");
selLang.className = "language-selector";
selLang.value = "python";
body.appendChild(selLang);

// Monaco editor container
const editorWrap = new MockNode("div");
editorWrap.className = "monaco-editor";
editorWrap.setAttribute("data-mode-id", "python");
const ta = new MockNode("textarea");
ta.className = "monaco-mouse-cursor-text";
ta.value = "def add(a, b):\\n    return 0";
editorWrap.appendChild(ta);
body.appendChild(editorWrap);

// Reset tracked mutations array so only actions during script execution are measured
mutations.length = 0;

// 3. Setup mock global window & document on coding practice route
global.window = {{
  location: {{
    hostname: "learning.ccbp.in",
    pathname: "/question/42",
    href: "https://learning.ccbp.in/question/42"
  }},
  addEventListener: () => {{}},
  removeEventListener: () => {{}}
}};
global.document = {{
  documentElement: docElement,
  head: head,
  body: body,
  getElementById: (id) => {{
    if (id === "__quizzy_hud_host__") return null;
    return docElement.querySelector('#' + id);
  }},
  querySelector: (sel) => docElement.querySelector(sel),
  querySelectorAll: (sel) => docElement.querySelectorAll(sel),
  createElement: (tag) => new MockNode(tag)
}};
global.chrome = {{
  runtime: {{
    onMessage: {{ addListener: () => {{}} }},
    sendMessage: () => {{}}
  }},
  storage: {{
    local: {{ get: (keys, cb) => cb({{}}) }}
  }}
}};

// 4. Load floating_hud.js
const hudCode = fs.readFileSync('{floating_hud_path}', 'utf8');
const hudResult = eval(hudCode);

// Assert floating HUD detected coding page and refused to mount in-page DOM elements
if (hudResult.mounted !== false) {{
  throw new Error("FAIL: floating_hud.js mounted DOM elements on coding page!");
}}
if (hudResult.codingMode !== true && (!hudResult.instance || hudResult.instance.isCodingMode !== true)) {{
  throw new Error("FAIL: floating_hud.js did not enter codingMode on /question/ route!");
}}

// 5. Load extractor.js
const extractorCode = fs.readFileSync('{extractor_path}', 'utf8');
const extractor = eval(extractorCode);

// Extract quiz from simulated coding page
const extRes = extractor.extractQuizFromDom();
if (extRes.q_type !== "code") {{
  throw new Error("FAIL: extractor did not detect q_type == 'code', got: " + extRes.q_type);
}}
if (extRes.language !== "python") {{
  throw new Error("FAIL: extractor did not detect language == 'python', got: " + extRes.language);
}}
if (!extRes.starter_code || !extRes.starter_code.includes("def add")) {{
  throw new Error("FAIL: starter_code not extracted properly: " + extRes.starter_code);
}}

// Call applyDomHighlight (must be strictly neutral on coding route)
const hlRes = extractor.applyDomHighlight({{ selected_index: 0 }});
if (hlRes.skipped !== true) {{
  throw new Error("FAIL: applyDomHighlight did not skip on proctored coding page!");
}}

// 6. Assert ZERO DOM mutations were triggered by floating HUD or Extractor
if (mutations.length > 0) {{
  console.error("VIOLATION: Recorded " + mutations.length + " DOM mutations:", JSON.stringify(mutations));
  process.exit(1);
}}

console.log("PASS: Exactly 0 DOM mutations recorded. Zero-DOM Anti-Detection verified.");
"""

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    if res.returncode != 0:
        print("STDOUT:", res.stdout)
        print("STDERR:", res.stderr)
    assert res.returncode == 0, f"Node.js Zero-DOM verification failed:\n{res.stderr}\n{res.stdout}"
    assert "Exactly 0 DOM mutations recorded" in res.stdout
    print("  -> Passed Test 1: Zero-DOM Anti-Detection confirmed 0 mutations on coding routes.")


def test_2_code_normalizer_and_python_ast_validation():
    """
    Test 2: Code Normalizer & Python AST Validation.
    Verify markdown code fence stripping, syntax validation via ast.parse,
    and canonical equivalence across whitespace/comment variances.
    """
    print("\n[Phase 7 - Test 2] Testing Code Normalizer & Python AST Validation...")

    # 1. Markdown code fence stripping
    raw_with_fence = "```python\ndef maxSubArray(nums):\n    return max(nums)\n```"
    norm = normalize_code_answer(raw_with_fence, language="python")
    assert norm.clean_code == "def maxSubArray(nums):\n    return max(nums)" if hasattr(norm, "clean_code") else norm.selected_text == "def maxSubArray(nums):\n    return max(nums)"
    assert norm.canonical_key.startswith("py:")
    print("  -> Passed markdown code fence stripping")

    # 2. Syntax validation
    valid_py = "def solve(a, b):\n    return a + b"
    norm_valid = normalize_code_answer(valid_py, language="python")
    assert norm_valid.selected_text == "def solve(a, b):\n    return a + b"
    assert norm_valid.canonical_key.startswith("py:")

    invalid_py = "def solve(a, b:\n    return a + b"
    with pytest.raises(NormalizationError):
        normalize_code_answer(invalid_py, language="python")
    print("  -> Passed Python syntax validation via AST parse")

    # 3. Canonical equivalence across whitespace & comments
    code_a = """
    # Helper to calculate product
    def calc_product(x, y):
        # Multiply numbers
        result = x * y
        return result
    """
    code_b = """
    def calc_product(x, y):
        result = x * y
        return result
    """
    norm_a = normalize_code_answer(code_a, language="python")
    norm_b = normalize_code_answer(code_b, language="python")

    assert norm_a.canonical_key == norm_b.canonical_key, (
        f"Expected identical canonical keys via AST unparse hash, got {norm_a.canonical_key} vs {norm_b.canonical_key}"
    )
    print("  -> Passed canonical equivalence: identical AST hash despite comments and indentation")


def test_3_cpp_java_starter_signature_preservation():
    """
    Test 3: C++ & Java Starter Signature Preservation & Balanced Brackets.
    Verify balanced bracket parsing and preservation of starter methods/classes.
    """
    print("\n[Phase 7 - Test 3] Testing C++/Java Signature Preservation & Balanced Brackets...")

    # 1. Balanced Brackets Check
    balanced_cpp = """
    #include <iostream>
    #include <vector>
    using namespace std;

    class Solution {
    public:
        int maxProfit(vector<int>& prices) {
            int min_p = prices[0];
            int max_p = 0;
            for (int p : prices) {
                min_p = min(min_p, p);
                max_p = max(max_p, p - min_p);
            }
            return max_p;
        }
    };
    """
    assert check_balanced_brackets(balanced_cpp) is True
    print("  -> Passed balanced brackets on valid C++ solution")

    # Braces in strings and comments ignored
    tricky_brackets = """
    // { This comment has unclosed brace
    string s = "}}"; /* unclosed { in comment */
    int x = (10 + 2);
    """
    assert check_balanced_brackets(tricky_brackets) is True

    # Real unclosed brace
    unclosed_cpp = "class Solution { public: int foo() { return 1; };"
    assert check_balanced_brackets(unclosed_cpp) is False
    print("  -> Passed unclosed bracket detection")

    # 2. Starter Signature Extraction and Validation
    starter_code = """
    class Solution {
    public:
        int lengthOfLongestSubstring(string s) {
            // write code here
        }
    };
    """
    sigs = extract_starter_signatures(starter_code)
    assert "Solution" in sigs
    assert "lengthOfLongestSubstring" in sigs

    good_solution = """
    class Solution {
    public:
        int lengthOfLongestSubstring(string s) {
            unordered_set<char> chars;
            return 0;
        }
    };
    """
    norm_good = normalize_code_answer(good_solution, language="cpp", starter_code=starter_code)
    assert norm_good.selected_text.strip()
    assert norm_good.canonical_key.startswith("cpp:")

    # Signature deleted / altered
    bad_solution = """
    class MyOwnClass {
    public:
        int wrongMethod(string s) {
            return 0;
        }
    };
    """
    with pytest.raises(NormalizationError):
        normalize_code_answer(bad_solution, language="cpp", starter_code=starter_code)
    print("  -> Passed C++ starter signature retention enforcement")


def test_4_backend_solve_code_and_caching():
    """
    Test 4: Backend /solve with q_type="code" & Caching.
    Verify end-to-end solve request with language and starter_code,
    and verify SQLite cache hit for identical code queries.
    """
    print("\n[Phase 7 - Test 4] Testing Backend /solve for q_type='code' & Caching...")
    from backend.core.llm_client import reset_key_quarantine, reset_model_quarantine
    reset_key_quarantine()
    reset_model_quarantine()

    client = TestClient(app)

    payload = {
        "subject": "Data Structures",
        "q_type": "code",
        "question": "Write a Python function to check whether a given string is a palindrome.",
        "options": [],
        "language": "python",
        "starter_code": "def is_palindrome(s: str) -> bool:\n    pass",
        "bypass_cache": True
    }

    # 1. Live solve (offline fixture mode in test)
    res = client.post("/solve", json=payload)
    assert res.status_code == 200, f"Expected 200 OK, got {res.status_code}: {res.text}"
    data = res.json()

    assert data["status"] == "ok"
    assert data["q_type"] == "code"
    assert data["language"] == "python"
    assert "def " in data["answer"] or "def is_palindrome" in data["answer"]
    assert data["selected_text"] == data["answer"]
    assert data["searxng_used"] is False
    print(f"  -> Passed live code solve. Answer length: {len(data['answer'])} chars")

    # 2. Subsequent solve with bypass_cache=False -> Cache Hit
    payload_cached = dict(payload)
    payload_cached["bypass_cache"] = False

    res2 = client.post("/solve", json=payload_cached)
    assert res2.status_code == 200
    data2 = res2.json()

    assert data2["cache_hit"] is True
    assert data2["answer"] == data["answer"]
    assert data2["latency_ms"] < 60.0
    print("  -> Passed SQLite cache hit for q_type='code'")

    # 3. Differing language -> Cache Miss
    payload_cpp = dict(payload_cached)
    payload_cpp["language"] = "cpp"
    payload_cpp["starter_code"] = "bool isPalindrome(string s) {}"

    res3 = client.post("/solve", json=payload_cpp)
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["language"] == "cpp"
    print("  -> Passed language separation in cache key hashing")


def test_5_monaco_ace_main_world_bridge_contract():
    """
    Test 5: Monaco/Ace MAIN-World Bridge Contract.
    Verify extension message contracts for READ_CODE_EDITOR_MAIN,
    WRITE_CODE_EDITOR_MAIN, and OPEN_POPUP_HUD across extension files.
    """
    print("\n[Phase 7 - Test 5] Auditing Monaco/Ace MAIN-World Bridge Contract...")

    # 1. Audit background.js
    bg_path = WORKSPACE_ROOT / "extension" / "background.js"
    with open(bg_path, "r", encoding="utf-8") as f:
        bg_code = f.read()

    assert 'action === "OPEN_POPUP_HUD"' in bg_code, "background.js missing OPEN_POPUP_HUD handler"
    assert 'chrome.windows.create' in bg_code, "background.js missing chrome.windows.create for popup HUD"
    assert 'action === "READ_CODE_EDITOR_MAIN"' in bg_code, "background.js missing READ_CODE_EDITOR_MAIN handler"
    assert 'action === "WRITE_CODE_EDITOR_MAIN"' in bg_code, "background.js missing WRITE_CODE_EDITOR_MAIN handler"
    assert 'world: "MAIN"' in bg_code, "background.js must specify world: 'MAIN' for editor bridge"
    assert 'window.monaco.editor.getModels()' in bg_code, "background.js missing Monaco getModels() query"
    assert 'window.ace.edit' in bg_code, "background.js missing Ace edit() query"
    print("  -> Passed background.js MAIN-world bridge contract audit")

    # 2. Audit executor.js
    exec_path = WORKSPACE_ROOT / "extension" / "executor.js"
    with open(exec_path, "r", encoding="utf-8") as f:
        exec_code = f.read()

    assert 'function executeCodeAction' in exec_code, "executor.js missing executeCodeAction function"
    assert 'qType === "code"' in exec_code, "executor.js executeAction missing qType === 'code' routing"
    assert 'executeCodeAction,' in exec_code, "executor.js missing executeCodeAction export"
    print("  -> Passed executor.js code execution contract audit")

    # 3. Audit sidepanel.html & sidepanel.js
    sp_html_path = WORKSPACE_ROOT / "extension" / "sidepanel.html"
    with open(sp_html_path, "r", encoding="utf-8") as f:
        sp_html = f.read()

    assert 'id="solution-code-container"' in sp_html, "sidepanel.html missing solution-code-container"
    assert 'id="solution-code-val"' in sp_html, "sidepanel.html missing solution-code-val"
    assert 'id="btn-copy-code"' in sp_html, "sidepanel.html missing btn-copy-code"
    assert 'value="code"' in sp_html, "sidepanel.html select-qtype missing value='code'"

    sp_js_path = WORKSPACE_ROOT / "extension" / "sidepanel.js"
    with open(sp_js_path, "r", encoding="utf-8") as f:
        sp_js = f.read()

    assert 'solution.q_type === "code"' in sp_js, "sidepanel.js missing code rendering branch"
    assert 'btnCopyCode' in sp_js, "sidepanel.js missing btnCopyCode click listener"
    assert 'WRITE_CODE_EDITOR_MAIN' in sp_js, "sidepanel.js missing WRITE_CODE_EDITOR_MAIN messaging"
    print("  -> Passed sidepanel UI and event wiring audit")


def test_6_full_regression_and_self_test():
    """
    Test 6: Full Regression & Eval Self-Test.
    Verify that all prior evaluation logic and test fixtures remain 100% operational.
    """
    print("\n[Phase 7 - Test 6] Running eval/run_eval.py --self-test...")

    eval_script = WORKSPACE_ROOT / "eval" / "run_eval.py"
    if eval_script.exists():
        res = subprocess.run(
            [sys.executable, str(eval_script), "--self-test"],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True
        )
        if res.returncode != 0:
            print("Eval stdout:", res.stdout)
            print("Eval stderr:", res.stderr)
        assert res.returncode == 0, f"eval/run_eval.py --self-test failed with code {res.returncode}"
        print("  -> Passed eval/run_eval.py --self-test (0 regressions)")
    else:
        print("  -> Note: eval/run_eval.py not found, skipping eval self-test.")
